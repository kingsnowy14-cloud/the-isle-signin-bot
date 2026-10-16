"""IronWolf Control Center: Discord-OAuth protected web dashboard.

The dashboard only edits allow-listed server settings and command toggles.
New module files are generated as downloadable templates and are never executed
automatically by the running bot.
"""
import hmac
import re
import asyncio
import html
import json
import os
import secrets
import threading
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import discord
from discord import app_commands
import requests
from flask import Flask, abort, redirect, request, session, url_for, make_response, send_file
from storage import data_path
from premium import PREMIUM_FEATURES, PLAN_FEATURES, account_plan, guild_has_feature, guild_trial_active

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
        "label": "Willkommensnachricht",
        "help": "Platzhalter: {mention}, {server}, {count}",
        "max_length": 1000,
    },
    "leave_message": {
        "label": "Auf-Wiedersehen-Nachricht",
        "help": "Platzhalter: {user}, {server}, {count}",
        "max_length": 1000,
    },
}
BOOL_SETTINGS = {
    "welcome_bots_enabled": "Willkommensnachrichten für Bots",
    "invite_tracker_enabled": "Invite-Tracker aktivieren",
    "automod_enabled": "AutoMod",
    "xp_enabled": "Level- und XP-System aktivieren",
    "anti_nuke_enabled": "Anti-Nuke-Schutz",
    "anti_raid_enabled": "Anti-Raid-Schutz",
}
CHANNEL_SETTINGS = {
    "welcome_channel": "Willkommenskanal",
    "goodbye_channel": "Auf-Wiedersehen-Kanal",
    "birthday_channel": "Geburtstagskanal",
    "levelup_channel": "Level-Up-Kanal",
    "invite_log_channel": "Invite-Tracker-Logkanal",
    "security_log_channel": "Sicherheits-Logkanal",
    "honeypot_channel": "Honeypot-Kanal",
    "verify_channel": "Verifizierungskanal",
    "registration_channel": "Anmeldekanal",
    "ticket_log_channel": "Ticket-Logkanal",
    "activity_log_channel": "Aktivitäts-Logkanal",
    "suggestion_channel": "Vorschlagskanal",
    "ticket_category": "Ticket-Kategorie",
    "log_channel_id": "Moderations-Logkanal",
}
ROLE_SETTINGS = {
    "support_role": "Support-Rolle",
    "verified_role": "Verifizierte Rolle",
    "unverified_role": "Unverifizierte Rolle",
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
        '<nav><a href="/" class="brand-mark">🐺 IRONWOLF <span class="muted">/ CONTROL CENTER</span></a>'
        + ('<a class="nav-server" href="/">🎮 Server wechseln</a>' if logged_in else '')
        + '<a href="/feedback">💬 Feedback</a>'
        + ('<a href="/admin/premium-servers">👑 Premium-Server</a>' if logged_in and is_premium_admin else '')
        + '<a href="https://discord.gg/T3AYmFmbrS" target="_blank" rel="noopener noreferrer">Discord ↗</a><span></span>'
        + ('<a href="/logout">Abmelden</a>' if logged_in else '<a href="/login">Mit Discord anmelden</a>')
        + '</nav>'
    )
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
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
.notice{{padding:16px 18px;border-radius:14px;background:linear-gradient(110deg,rgba(91,141,255,.08),rgba(114,199,255,.055));border:1px solid rgba(154,177,211,.2);margin:19px 0;color:#dce6f5}}code{{overflow-wrap:anywhere;background:#080d17;border:1px solid var(--border);border-radius:7px;padding:3px 6px;font-size:.92em}}form{{margin-top:12px}}form button{{width:auto}}.card form>button{{margin-top:20px}}select{{cursor:pointer}}
.section-nav{{display:flex;flex-wrap:wrap;gap:9px;margin:20px 0 25px;padding:12px;background:rgba(12,18,30,.7);border:1px solid var(--border);border-radius:16px}}.section-nav a{{display:inline-flex;padding:8px 12px;border-radius:10px;text-decoration:none;font-size:13px;font-weight:800;background:rgba(255,255,255,.025);border:1px solid transparent;transition:background .15s,border-color .15s}}.section-nav a:hover{{border-color:rgba(114,199,255,.4);background:rgba(114,199,255,.07)}}section[id]{{scroll-margin-top:100px}}
.command-groups{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:14px;align-items:start}}.command-group{{border:1px solid var(--border);background:rgba(7,12,21,.5);border-radius:15px;padding:14px;min-width:0}}.command-group h3{{margin:2px 0 10px;padding-bottom:10px;border-bottom:1px solid var(--border)}}.command-group .row{{gap:8px;padding:10px 0}}.command-group .row strong{{font-size:13px;overflow-wrap:anywhere}}.command-group .check label{{font-size:12px}}
@media(max-width:650px){{nav{{padding:12px 15px;gap:10px}}nav a:first-child{{font-size:15px}}main{{margin:25px auto;padding:0 14px 34px}}.card{{padding:19px;border-radius:16px}}.row{{align-items:flex-start}}.section-nav{{gap:6px;padding:9px}}.section-nav a{{font-size:12px;padding:7px 9px}}button,.button{{max-width:100%;white-space:normal}}}}
\n/* Gaming HUD look */\nbody{{background-image:radial-gradient(ellipse at 12% 0%,rgba(47,112,255,.22),transparent 35%),radial-gradient(ellipse at 90% 12%,rgba(0,255,190,.075),transparent 28%),linear-gradient(180deg,#080d19 0%,#070a12 62%,#04060b 100%)}}\n.card{{border-radius:16px;border-color:rgba(103,143,208,.22);background:linear-gradient(145deg,rgba(16,27,48,.96),rgba(8,14,26,.98));box-shadow:0 16px 45px rgba(0,0,0,.38),inset 0 1px 0 rgba(255,255,255,.025)}}\n.card::before{{height:3px;background:linear-gradient(90deg,#53a0ff,#438fd0,#72c7ff,transparent)}}\n.pill{{background:rgba(62,133,255,.12);border-color:rgba(83,160,255,.32);color:#a9d0ff;letter-spacing:1.4px}}\nh1{{text-shadow:0 0 34px rgba(65,129,255,.16)}}\n.button{{background:linear-gradient(120deg,#73b7ff,#9b8cff 55%,#64efd0);color:#07111e;border:0;box-shadow:0 8px 26px rgba(72,136,255,.18)}}\n.button:hover,button:hover{{box-shadow:0 10px 30px rgba(72,136,255,.27)}}\ninput[type=file]{{width:100%;padding:12px;border:1px dashed rgba(103,164,255,.45);border-radius:12px;background:rgba(32,66,112,.12);color:var(--text)}}\n@media(max-width:760px){{nav{{flex-wrap:wrap}}nav span{{display:none}}nav a:first-child{{width:100%}}}}\n@media(prefers-reduced-motion:reduce){{*,*::before,*::after{{scroll-behavior:auto!important;transition:none!important;animation:none!important}}}}
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
{custom_theme_css}</style></head><body>{nav}<main>{body}<p class="muted"><small>IronWolf · Created by KingSnowy</small></p></main></body></html>"""


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
        status = "🟢 Aktiv" if item.get("enabled") else "🔴 Deaktiviert"
        rows.append(
            '<div class="row"><div><strong><code>' + html.escape(item["name"]) +
            '</code></strong><br><small>' + html.escape(item.get("kind", "Befehl")) +
            ' · ' + html.escape(item.get("cog", "Allgemein")) +
            '</small></div><small>' + status + '</small></div>'
        )
    if not rows:
        content = '<p class="muted">Für dieses Modul wurden keine direkt zuordenbaren Discord-Befehle erkannt. Die vollständige Liste findest du unter <a href="/guild/' + html.escape(guild_id) + '/commands">Befehle &amp; Slash-Commands</a>.</p>'
    else:
        content = '<p class="muted">Die Befehle dieses Moduls im Discord-Chat. Deaktivierte Befehle sind markiert; verwalte sie in der Befehlsübersicht.</p>' + ''.join(rows)
    return '<section class="card module-command-footer"><h2>⌘ Zugehörige Discord-Befehle</h2>' + content + '<p><a class="button secondary" href="/guild/' + html.escape(guild_id) + '/commands">Alle Befehle verwalten →</a></p></section>'


def _render(title, body, status=200):
    body += _module_command_footer()
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


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
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
        '<div class="notice"><small>Discord-Server</small><h2>' + str(guild_count) + '</h2></div>'
        '<div class="notice"><small>Erfasste Mitglieder</small><h2>' + str(member_count) + '</h2></div>'
        '<div class="notice"><small>Befehle</small><h2>' + str(prefix_count + slash_count) + '</h2><small>' + str(prefix_count) + ' Prefix · ' + str(slash_count) + ' Slash</small></div>'
        '</div>'
        '<p><a class="button" href="' + invite_url + '" target="_blank" rel="noopener noreferrer">🐺 IronWolf einladen</a> '
        '<a class="button secondary" href="https://discord.gg/T3AYmFmbrS" target="_blank" rel="noopener noreferrer">💬 Support öffnen</a></p>'
        '<p class="muted"><small>Hinweis: Die Werte aktualisieren sich beim Neuladen der Seite.</small></p>'
        '</section>'
    )


@app.route("/")
def index():
    login = _require_login()
    if login:
        body = """<section class="card hero"><span class="pill">CONTROL CENTER</span><h1 style="margin-top:16px">Dein Server. Deine Regeln.</h1>
<p>Verwalte deine IronWolf-Server an einem Ort: Einstellungen, Befehle und Modulvorlagen.</p>
<div class="notice">🔐 Die Anmeldung erfolgt über Discord. Du kannst nur Server verwalten, auf denen du „Server verwalten“ oder Administrator-Rechte besitzt und auf denen der Bot online ist.</div>
<a class="button" href="/login">Mit Discord anmelden</a>
<div class="notice">✨ <strong>Premium kostet 5 € pro Jahr.</strong> Fragen oder Nachrichten? <a href="https://discord.gg/T3AYmFmbrS" target="_blank" rel="noopener noreferrer">Tritt unserem Discord bei</a>.</div>
<h2>Was du hier machen kannst</h2><ul><li>Serverbezogene Einstellungen mit Formularen ändern</li><li>Prefix- und Slash-Befehle einzeln aktivieren/deaktivieren</li><li>Neue Python-Modulvorlagen erstellen und herunterladen</li></ul></section>"""
        if not _oauth_ready():
            body += '<div class="card"><h2>Einmalige Einrichtung nötig</h2><p>Setze in Railway die Variablen <code>DISCORD_CLIENT_ID</code>, <code>DISCORD_CLIENT_SECRET</code>, <code>DASHBOARD_REDIRECT_URI</code> und <code>DASHBOARD_SECRET_KEY</code>. Als Redirect URI muss exakt die öffentliche URL plus <code>/callback</code> in der Discord Developer Portal OAuth2-Einstellung stehen.</p></div>'
        body += _public_status_panel()
        body += '<section class="card"><h2>📜 Befehle & Funktionen</h2><p>Nach der Discord-Anmeldung kannst du die vorhandenen Prefix- und Slash-Befehle ansehen und verwalten. Die Liste wird aus den tatsächlich geladenen Bot-Befehlen erstellt.</p><a class="button secondary" href="/login">Befehle im Dashboard öffnen →</a></section>'
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
    body = '<section class="card hero"><span class="pill">WILLKOMMEN BEI IRONWOLF</span><h1 style="margin-top:14px">Dein Control Center 🐺</h1><p>Hier steuerst du deine verbundenen Discord-Server bequem über eine Oberfläche.</p><div class="notice">✨ <strong>Premium kostet 5 € pro Jahr.</strong> <a href="https://discord.gg/T3AYmFmbrS" target="_blank" rel="noopener noreferrer">Nachrichten auf Discord ↗</a></div><div class="grid"><div><h2>So funktioniert es</h2><ol><li>Wähle unten den Server aus, den du verwalten möchtest.</li><li>Unter <strong>Einstellungen</strong> legst du Kanäle, Rollen und Serveroptionen fest.</li><li>Unter <strong>Module</strong> aktivierst oder deaktivierst du Funktionen für diesen Server.</li><li>Öffne <strong>Befehle</strong>, um Prefix- und Slash-Befehle gruppiert zu verwalten.</li><li>Nutze die <strong>Aktionszentrale</strong> für unterstützte Moderationsaktionen.</li></ol></div><div><h2>Wichtige Hinweise</h2><ul><li>Du brauchst Administrator- oder Server-verwalten-Rechte.</li><li>Der Bot muss auf dem Server online sein.</li><li>Änderungen an Modulen und Befehlen gelten nur für den ausgewählten Server.</li><li>Gefährliche Aktionen benötigen zusätzliche Bestätigung und passende Rechte.</li></ul></div></div></section><h2>Deine Server</h2><p class="muted">Wähle einen Server, um seine Einstellungen zu öffnen.</p>'
    body += _public_status_panel()
    body += '<p><a class="button" href="/premium">✨ Premium verwalten →</a></p>'
    body += '<div class="grid">' + ("".join(cards) or '<div class="card">Keine passenden Server gefunden. Prüfe deine Discord-Rechte und ob IronWolf auf dem Server ist.</div>') + '</div>'
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
            abort(400, "Sicherheitsprüfung fehlgeschlagen. Bitte lade die Seite neu.")
        rating = request.form.get("rating", "")
        category = request.form.get("category", "Allgemein")
        allowed_categories = ("Allgemein", "Dashboard", "Ticketsystem", "Verifizierung", "Module")
        if rating not in ("like", "dislike"):
            abort(400, "Bitte wähle 👍 oder 👎.")
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
    notice = '<div class="notice">Danke! Dein Feedback wurde anonym gespeichert.</div>' if request.args.get("sent") == "1" else ""
    body = '<section class="card"><span class="pill">ANONYMES FEEDBACK</span><h1>Wie gefällt dir IronWolf?</h1><p>Es werden keine Discord-ID, kein Name und keine IP-Adresse im Feedback-Datensatz gespeichert. Die Statistik ist zusammengefasst.</p>' + notice + '<form method="post">' + _csrf_field() + '<label>Bereich</label><select name="category"><option>Allgemein</option><option>Dashboard</option><option>Ticketsystem</option><option>Verifizierung</option><option>Module</option></select><label>Bewertung</label><p><label><input type="radio" name="rating" value="like" required> 👍 Gefällt mir</label><label><input type="radio" name="rating" value="dislike" required> 👎 Verbesserungsbedarf</label></p><label>Kommentar (optional; bitte keine persönlichen Daten eintragen)</label><textarea name="comment" maxlength="500" placeholder="Was können wir verbessern?"></textarea><button type="submit">Anonym absenden</button></form></section><section class="card"><h2>Feedback-Statistik</h2><div class="grid"><div class="notice">Alle Bewertungen<h2>' + str(total) + '</h2></div><div class="notice">👍 Gefällt<h2>' + str(likes) + '</h2></div><div class="notice">👎 Verbesserungsbedarf<h2>' + str(dislikes) + '</h2></div></div><h3>Nach Bereich</h3>' + rows + '</section>'
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


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/premium")
def premium_page():
    login = _require_login()
    if login:
        return login
    user_id = str(session.get("user_id", ""))
    plan = account_plan(user_id)
    labels = {"free": "Free", "premium": "Premium", "premium_plus": "Premium"}
    included_features = set(PREMIUM_FEATURES) if plan in {"premium", "premium_plus"} else set()
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
        '<section class="card"><h2>Premium-Pläne</h2><p><strong>Premium: 5 € pro Jahr.</strong> Ticket-System Pro und Custom Branding.</p>'
        '<p><strong>Premium: 5 € pro Jahr.</strong> Enthält alle Module: Ticket-System Pro, Custom Branding, Security Pro, Automation Pro und Invite Tracker.</p>'
        '<p class="muted">Öffne die Premium-Verwaltung eines Servers, um deinen Plan mit ihm zu verknüpfen. Dort führen direkte Schaltflächen zu den jeweiligen Einstellungen.</p>'
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
    labels = {"free": "Free", "premium": "Premium", "premium_plus": "Premium"}
    rows = []
    for feature, label in PREMIUM_FEATURES.items():
        if feature == "branding":
            continue
        manual_server_premium = server_override in PLAN_FEATURES and server_override != "free"
        entitled = trial_active or (feature in PLAN_FEATURES.get(plan, frozenset()) and (owner_matches or manual_server_premium))
        if trial_active:
            status = "Aktiv · 15-Tage-Testphase"
        else:
            status = "Aktiv für diesen Server" if entitled else ("Konto berechtigt, Server noch nicht verknüpft" if feature in PLAN_FEATURES.get(plan, frozenset()) and not owner_matches else "Nicht im Kontoplan enthalten")
        feature_links = {
            "ticket": ("/guild/" + gid + "/tickets", "Ticket-System verwalten"),
            "branding": ("/guild/" + gid + "/tickets", "Ticket-Branding einstellen"),
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
        body = '<section class="card"><h1>⚙️ Automation Pro</h1><p>Geplante Nachrichten sind eine Premium-Funktion. Verknüpfe zuerst dein berechtigtes Konto auf der Premium-Seite.</p><a class="button" href="/guild/' + gid + '/premium">Premium verwalten →</a></section>'
    else:
        channel_options = '<option value="">— Textkanal auswählen —</option>'
        for channel in guild.text_channels:
            selected = " selected" if str(job.get("channel_id") or "") == str(channel.id) else ""
            channel_options += '<option value="' + str(channel.id) + '"' + selected + '>' + html.escape(channel.name) + '</option>'
        checked = " checked" if job.get("enabled") else ""
        body = (
            '<section class="card hero"><span class="pill">PREMIUM+</span><h1 style="margin-top:14px">⚙️ Automation Pro</h1>'
            '<p>Veröffentliche automatisch wiederkehrende Server-Ankündigungen. Die erste Nachricht wird erst nach dem gewählten Intervall gesendet.</p>'
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
            abort(400, "Titel oder Beschreibung sind zu lang oder leer.")
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
        '<label for="description">Panel-Beschreibung</label><textarea id="description" name="description" maxlength="3500">' + html.escape(str(cfg.get("id_card_description", defaults["id_card_description"]))) + '</textarea>'
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
        '<p><a class="button secondary" href="/guild/' + gid + '">← Serverübersicht</a></p></section>'
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
            _audit_dashboard_change(guild.id, "Modul-Manager", session.get("user_id"), "Aktive Module: " + ", ".join(sorted(valid_modules - disabled)))
            return redirect(url_for("guild_page", guild_id=gid, section="modules"))
        if action == "settings":
            # Keep the in-memory configuration unchanged if persistence fails.
            import copy
            original_cfg = copy.deepcopy(cfg)
            try:
                settings_section = request.form.get("settings_section", "general")
                if settings_section == "general":
                    for key, meta in TEXT_SETTINGS.items():
                        if key in request.form:
                            value = request.form.get(key, "").strip()
                            if len(value) > meta["max_length"]:
                                abort(400, f"{meta['label']} ist zu lang.")
                            cfg[key] = value
                    for key in ("welcome_bots_enabled", "welcome_embed", "leave_enabled", "leave_embed", "automod_enabled", "xp_enabled"):
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
                return _render("Speichern fehlgeschlagen", '<div class="card"><h1>Änderungen konnten nicht gespeichert werden</h1><p>Die Einstellungen wurden zurückgesetzt. Prüfe die Railway-Logs nach <code>[DASHBOARD] Failed to save settings</code>, um den genauen technischen Fehler zu sehen.</p><a class="button secondary" href="/guild/' + gid + '">Zurück zu den Einstellungen</a></div>', 500)
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
    section = request.args.get("section", "overview")
    settings_html = ""
    if section in ("general", "channels", "roles", "voice", "security"):
        settings_html = '<section class="card"><h2>⚙️ ' + {"general":"Allgemeine Einstellungen","channels":"Kanäle & Logs","roles":"Rollen","voice":"Temporäre Voice-Kanäle","security":"Schutz & Moderation"}[section] + '</h2><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="settings"><input type="hidden" name="settings_section" value="' + section + '">'
        if section == "general":
            settings_html += '<p class="muted">👑 Der Invite-Tracker ist ein Premium-Modul. Aktiviere ihn nur, wenn für diesen Server ein entsprechender Premium-Zugang besteht.</p>'
            for key, label in TEXT_SETTINGS.items():
                settings_html += f'<label for="{key}">{label["label"]}</label><small>{label["help"]}</small><textarea id="{key}" name="{key}" maxlength="{label["max_length"]}">{html.escape(str(cfg.get(key, "")))}</textarea>'
            for key in ("welcome_bots_enabled", "welcome_embed", "leave_enabled", "leave_embed", "automod_enabled", "xp_enabled"):
                label = {"welcome_embed": "Willkommensnachricht als Embed senden", "leave_enabled": "Auf-Wiedersehen-Nachricht aktivieren", "leave_embed": "Auf-Wiedersehen-Nachricht als Embed senden"}.get(key, BOOL_SETTINGS.get(key, key))
                checked = " checked" if cfg.get(key, False) else ""
                settings_html += f'<div class="check"><input type="checkbox" id="{key}" name="{key}"{checked}><label for="{key}">{html.escape(label)}</label></div>'
            settings_html += "<h3>AutoMod & XP konfigurieren</h3>"
            for key, label, minimum, maximum, default in [("automod_threshold", "Nachrichten bis Spam-Erkennung", 3, 20, 6), ("automod_window_seconds", "Spam-Zeitfenster in Sekunden", 3, 30, 8), ("automod_timeout_minutes", "Timeout in Minuten", 1, 60, 2), ("xp_cooldown_seconds", "XP-Cooldown in Sekunden", 10, 600, 60), ("xp_min", "Minimale XP pro Nachricht", 1, 100, 5), ("xp_max", "Maximale XP pro Nachricht", 1, 100, 15)]:
                value = max(minimum, min(maximum, int(cfg.get(key, default) or default)))
                settings_html += f'<label for="{key}">{label}</label><input id="{key}" name="{key}" type="number" min="{minimum}" max="{maximum}" value="{value}" required>'
            if guild_has_feature(cfg, "invite_tracker"):
                checked = " checked" if cfg.get("invite_tracker_enabled", False) else ""
                settings_html += f'<div class="check"><input type="checkbox" id="invite_tracker_enabled" name="invite_tracker_enabled"{checked}><label for="invite_tracker_enabled">{html.escape(BOOL_SETTINGS["invite_tracker_enabled"])}</label></div>'
                settings_html += '<label for="invite_log_channel">Invite-Tracker: Logkanal auswählen</label><small>Hier werden erkannte Einladungen und Einladungsnutzungen protokolliert.</small><select id="invite_log_channel" name="invite_log_channel">' + options(channels, cfg.get("invite_log_channel")) + '</select>'
            else:
                settings_html += '<div class="check"><input type="checkbox" id="invite_tracker_locked" disabled><label for="invite_tracker_locked">🔒 Invite-Tracker aktivieren (Premium erforderlich)</label></div><p class="muted">Der Invite-Tracker bleibt gesperrt, bis dieser Server aktives Premium hat. <a href="/guild/' + gid + '/premium">Premium verwalten →</a></p>'
        if section == "channels":
            for key, label in CHANNEL_SETTINGS.items():
                if key == "ticket_category":
                    continue
                settings_html += f'<label for="{key}">{html.escape(label)}</label><select id="{key}" name="{key}">{options(channels, cfg.get(key))}</select>'
        if section == "roles":
            for key, label in ROLE_SETTINGS.items():
                settings_html += f'<label for="{key}">{html.escape(label)}</label><select id="{key}" name="{key}">{options(roles, cfg.get(key))}</select>'
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
        if section == "channels":
            settings_html += f'<label for="ticket_category">{CHANNEL_SETTINGS["ticket_category"]}</label><select id="ticket_category" name="ticket_category">{options([(category.id, category.name) for category in guild.categories], cfg.get("ticket_category"))}</select>'
        settings_html += '<button type="submit">Änderungen speichern</button></form></section>'
        if section == "roles":
            settings_html += '<section class="card"><h3>＋ Discord-Rolle erstellen</h3><p class="muted">Erstellt eine echte Discord-Rolle auf deinem Server. Danach kannst du sie im Nachrichten-Editor als Rollen-Button auswählen.</p><form method="post" action="/guild/' + gid + '/roles/create">' + _csrf_field() + '<label for="role_name">Rollenname</label><input id="role_name" name="role_name" maxlength="100" required placeholder="z. B. Gaming, Ankündigungen oder Verifiziert"><label for="role_color">Rollenfarbe</label><input id="role_color" name="role_color" type="color" value="#5865F2" style="height:48px;padding:5px"><button type="submit">🎭 Discord-Rolle erstellen</button></form></section>'
    module_records = _module_records(guild.id)
    modules_html = '<section class="card" id="modules"><h2>🧩 Module aktivieren/deaktivieren</h2><p class="muted">Gilt nur für diesen Server. Ein deaktiviertes Modul blockiert seine Cog-Befehle; andere Server bleiben unverändert.</p><form method="post">' + _csrf_field() + '<input type="hidden" name="action" value="modules">'
    for item in module_records:
        checked = " checked" if item["enabled"] else ""
        modules_html += f'<div class="row"><strong>{html.escape(item["name"])}</strong><div class="check"><input type="checkbox" id="module-{html.escape(item["name"])}" name="module:{html.escape(item["name"])}"{checked}><label for="module-{html.escape(item["name"])}">Aktiv</label></div></div>'
    if not module_records:
        modules_html += '<p class="muted">Aktuell sind keine Cog-Module registriert.</p>'
    modules_html += '<button type="submit">Module speichern</button></form></section>'
    module_form = '<section class="card" id="module-template"><h2>🛠️ Modul-Vorlage erstellen</h2><p class="muted">Erstellt eine herunterladbare, sichere discord.py-Cog-Vorlage. Die Datei wird nicht automatisch geladen.</p><form method="post" action="/create-module">' + _csrf_field() + f'<input type="hidden" name="guild_id" value="{gid}"><label for="module_name">Modulname</label><input id="module_name" name="module_name" type="text" required maxlength="40" pattern="[A-Za-z][A-Za-z0-9_]*" placeholder="mein_modul"><label for="description">Beschreibung</label><input id="description" name="description" type="text" maxlength="120" placeholder="Was macht das Modul?"><label for="command_name">Beispiel-Befehl</label><input id="command_name" name="command_name" type="text" maxlength="32" pattern="[a-z][a-z0-9-]*" value="mein-modul" required><button type="submit">Vorlage erstellen</button></form></section>'
    section_links = [
        ("general", "⚙️ Willkommen & Allgemeines", "Willkommensnachricht und grundlegende Optionen"),
        ("channels", "📡 Kanäle & Logs", "Log-, Ticket-, Vorschlags-, Invite- und Verifizierungskanäle"),
        ("roles", "🎭 Rollen", "Support-, Verifizierungs- und Sicherheitsrollen"),
        ("voice", "🔊 Temporäre Voice-Kanäle", "Trigger und Zielkategorie für temporäre Räume"),
    ]
    settings_cards = ''.join('<a class="card dashboard-card" href="/guild/' + gid + '?section=' + key + '"><h2>' + title + '</h2><p>' + desc + '</p><span class="card-link">Einstellungen öffnen →</span></a>' for key, title, desc in section_links)
    if section != "overview" and section not in {"general", "channels", "roles", "voice", "security", "modules", "template"}:
        section = "overview"
    body = f'<span class="pill">IRONWOLF · SERVER CONTROL</span><h1 style="margin-top:12px">🐺 {html.escape(guild.name)}</h1><p class="muted">Server-ID: {guild.id} · Verwalte Einstellungen, Module und Befehle zentral.</p><div class="notice"><strong>⚡ Schnellzugriff</strong><p style="margin:4px 0 10px">Aktionen direkt auf Discord ausführen oder gezielt zu einem Verwaltungsbereich springen.</p><a class="button" href="/guild/{gid}/actions">Aktionszentrale öffnen →</a> <a class="button secondary" href="/guild/{gid}/tickets">Ticket-System verwalten →</a> <a class="button secondary" href="/guild/{gid}/verification">Verifizierung verwalten →</a> <a class="button secondary" href="/guild/{gid}/premium">✨ Premium verwalten →</a> <a class="button secondary" href="/guild/{gid}/automation">⚙️ Automation Pro →</a> <a class="button secondary" href="/guild/{gid}/messages">✉️ Bot-Nachrichten →</a> <a class="button secondary" href="/guild/{gid}/branding">🎨 Server-Design →</a> <a class="button secondary" href="/guild/{gid}/ping-protection">🛡️ Ping-Schutz →</a> <a class="button secondary" href="/guild/{gid}/ausweis">🪪 Ausweissystem →</a></div><h2 class="dashboard-group-title">⭐ Wichtigste Funktionen</h2><p class="muted">Die am häufigsten benötigten Einstellungen stehen ganz oben.</p><div class="grid"><a class="card dashboard-card" href="/guild/{gid}?section=general"><h2>👋 Willkommen & Begrüßung</h2><p>Willkommensnachrichten und grundlegende Serveroptionen zuerst konfigurieren.</p><span class="card-link">Willkommen einstellen →</span></a><a class="card dashboard-card" href="/guild/{gid}/verification"><h2>🔐 Verifizierung</h2><p>Verifizierungsnachricht, Kanal und Rolle einrichten.</p><span class="card-link">Verifizierung verwalten →</span></a><a class="card dashboard-card" href="/guild/{gid}/voice-support"><h2>🎧 Voice-Support</h2><p>Support-Warteraum, Team-Rolle und private Support-Räume verwalten.</p><span class="card-link">Support einstellen →</span></a><a class="card dashboard-card" href="/guild/{gid}/tickets"><h2>🎟️ Support-Tickets</h2><p>Ticket-Panel, Support-Team und Ticket-Kategorie konfigurieren.</p><span class="card-link">Tickets verwalten →</span></a><a class="card dashboard-card" href="/guild/{gid}/messages"><h2>✉️ Nachrichten & Buttons</h2><p>Bot-Nachrichten, Rollen-Buttons und Link-Buttons erstellen.</p><span class="card-link">Nachricht erstellen →</span></a></div><div class="section-nav"><a href="/guild/{gid}">🏠 Übersicht</a><a href="/guild/{gid}?section=general">👋 Willkommen</a><a href="/guild/{gid}/voice-support">🎧 Support</a><a href="/guild/{gid}/verification">🔐 Verifizierung</a><a href="/guild/{gid}/tickets">🎟️ Tickets</a><a href="/guild/{gid}/messages">✉️ Nachrichten</a><a href="/guild/{gid}/branding">🎨 Server-Design</a><a href="/guild/{gid}?section=security">🛡️ Sicherheit</a><a href="/guild/{gid}/ping-protection">🛡️ Ping-Schutz</a><a href="/guild/{gid}/ausweis">🪪 Ausweis</a><a href="/guild/{gid}?section=channels">📡 Kanäle & Logs</a><a href="/guild/{gid}?section=roles">🎭 Rollen</a><a href="/guild/{gid}?section=voice">🔊 Voice</a><a href="/guild/{gid}/economy">💰 Wirtschaft</a><a href="/guild/{gid}/birthdays">🎂 Geburtstage</a><a href="/guild/{gid}/automation">⚙️ Automation</a><a href="/guild/{gid}/messages">✉️ Bot-Nachrichten</a><a href="/guild/{gid}/commands">⌘ Befehle</a><a href="/guild/{gid}/audit-log">📜 Audit-Log</a><a href="/guild/{gid}/stats">📊 Live-Statistiken</a><a href="/guild/{gid}?section=modules">🧩 Module</a></div>{settings_html if section != "overview" else '<div class="dashboard-metrics"><div class="dashboard-metric"><strong>' + str(len(module_records)) + '</strong><span>Erkannte Module</span></div><div class="dashboard-metric"><strong>' + str(sum(1 for item in module_records if item["enabled"])) + '</strong><span>Aktive Module</span></div><div class="dashboard-metric"><strong>' + str(sum(1 for item in _command_records(guild.id) if item["enabled"])) + '</strong><span>Aktive Befehle</span></div><div class="dashboard-metric"><strong>' + str(sum(1 for item in _command_records(guild.id) if not item["enabled"])) + '</strong><span>Deaktivierte Befehle</span></div></div><h2 class="dashboard-group-title">🛡️ Sicherheit & Grundeinstellungen</h2><div class="grid">' + settings_cards + '<a class="card dashboard-card" href="/guild/' + gid + '?section=security"><h2>🛡️ Schutz & Moderation</h2><p>Anti-Raid, Anti-Nuke und konfigurierbare Schutzschwellen.</p><span class="card-link">Schutz verwalten →</span></a><a class="card dashboard-card" href="/guild/' + gid + '/economy"><h2>💰 Wirtschaftssystem</h2><p>Übersicht der vorhandenen Geld- und Shop-Funktionen.</p><span class="card-link">Wirtschaft öffnen →</span></a><a class="card dashboard-card" href="/guild/' + gid + '/birthdays"><h2>🎂 Geburtstagskalender</h2><p>Geburtstagskanal, Nachricht und gespeicherte Geburtstage verwalten.</p><span class="card-link">Geburtstage verwalten →</span></a></div><h2 class="dashboard-group-title">👥 Serverfunktionen</h2><div class="grid"><a class="card dashboard-card" href="/guild/' + gid + '/tickets"><h2>🎟️ Tickets</h2><p>Ticket-Panels, Support-Rollen, Kategorien und Protokollierung.</p><span class="card-link">Tickets verwalten →</span></a><a class="card dashboard-card" href="/guild/' + gid + '/verification"><h2>🔐 Verifizierung</h2><p>Verifizierungspost, Rollen und zugehörige Einstellungen.</p><span class="card-link">Verifizierung verwalten →</span></a><a class="card dashboard-card" href="/guild/' + gid + '/actions"><h2>🎮 Aktionszentrale</h2><p>Moderation und schnelle Serveraktionen direkt aus dem Dashboard.</p><span class="card-link">Aktionen öffnen →</span></a><a class="card dashboard-card" href="/guild/' + gid + '/audit-log"><h2>📜 Dashboard-Audit-Log</h2><p>Nachvollziehen, welche Einstellungen im Dashboard geändert wurden.</p><span class="card-link">Audit-Log öffnen →</span></a><a class="card dashboard-card" href="/guild/' + gid + '/stats"><h2>📊 Live-Statistiken</h2><p>Mitglieder, Befehle, Nachrichtenaktivität und gespeicherte Voice-Zeit.</p><span class="card-link">Statistiken öffnen →</span></a><a class="card dashboard-card" href="/guild/' + gid + '/messages"><h2>✉️ Bot-Nachrichten & Buttons</h2><p>Nachrichten senden und HTTPS-Link- oder Rollen-Buttons ergänzen.</p><span class="card-link">Nachricht erstellen →</span></a><a class="card dashboard-card" href="/guild/' + gid + '/premium"><h2>✨ Premium & Funktionen</h2><p>Premium-Status und freigeschaltete Funktionen dieses Servers.</p><span class="card-link">Premium verwalten →</span></a></div><h2 class="dashboard-group-title">⚙️ Automatisierung & Verwaltung</h2><div class="grid"><a class="card dashboard-card" href="/guild/' + gid + '/automation"><h2>📣 Nachrichten & Automation</h2><p>Wiederkehrende Ankündigungen und gespeicherte Nachrichtenvorlagen.</p><span class="card-link">Automation öffnen →</span></a><a class="card dashboard-card" href="/guild/' + gid + '/commands"><h2>⌘ Befehle & Rechte</h2><p>Prefix- und Slash-Befehle pro Server aktivieren oder deaktivieren.</p><span class="card-link">Befehle verwalten →</span></a><a class="card dashboard-card" href="/guild/' + gid + '?section=modules"><h2>🧩 Modul-Manager</h2><p>Verfügbare Bot-Module serverbezogen aktivieren oder deaktivieren.</p><span class="card-link">Module verwalten →</span></a><a class="card dashboard-card" href="/guild/' + gid + '?section=template"><h2>＋ Modul-Vorlage</h2><p>Sichere herunterladbare Vorlage für eigene discord.py-Cogs erstellen.</p><span class="card-link">Vorlage erstellen →</span></a><a class="card dashboard-card" href="/guild/' + gid + '/voice-support"><h2>🎧 Sprach-Support</h2><p>Warteraum, Team-Rolle, Benachrichtigungen und private Sprachkanäle konfigurieren.</p><span class="card-link">Sprach-Support einstellen →</span></a></div>'}<div class="grid">{modules_html if section == "modules" else ''}{module_form if section == "template" else ''}</div>'
    return _render(guild.name + " · Control Center", body)



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
        abort(400, "Bitte wähle eine gültige Rollenfarbe.")
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
    role_options = ''.join('<option value="' + str(role.id) + '"' + (' selected' if str(role.id) in protected_roles else '') + '>' + html.escape(role.name) + '</option>' for role in sorted(guild.roles, key=lambda item: item.position, reverse=True) if not role.is_default() and not role.managed)
    member_options = ''.join('<option value="' + str(member.id) + '"' + (' selected' if str(member.id) in protected_members else '') + '>' + html.escape(member.display_name) + ' (' + str(member.id) + ')</option>' for member in sorted(guild.members, key=lambda item: item.display_name.casefold()) if not member.bot)
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
        '<label for="protected_role_ids">Rollen zum Schützen auswählen</label><select id="protected_role_ids" name="protected_role_ids" multiple size="8" style="min-height:170px">' + role_options + '</select>'
        '<small class="muted">Mehrere Rollen mit Strg (Windows) oder Cmd (Mac) auswählen. Die Auswahl wird erst nach „Speichern“ übernommen.</small>'
        '<h3>Aktuell geschützte Rollen</h3>' + ''.join(protected_role_rows) +
        '<label for="protected_member_ids">Personen zum Schützen auswählen</label><select id="protected_member_ids" name="protected_member_ids" multiple size="8" style="min-height:170px">' + member_options + '</select>'
        '<small class="muted">Mehrere Personen mit Strg (Windows) oder Cmd (Mac) auswählen.</small>' + protected_member_summary +
        '<p class="notice">Hinweis: Mitglieder mit „Nachrichten verwalten“ sind ausgenommen. IronWolf braucht im betroffenen Kanal „Nachrichten verwalten“, um Pings zu löschen. DMs können durch Privatsphäre-Einstellungen blockiert sein.</p>'
        '<button type="submit">💾 Ping-Schutz speichern</button></form><p><a class="button secondary" href="/guild/' + gid + '">← Serverübersicht</a></p></section>'
    )
    return _render(guild.name + " · Ping-Schutz", body)

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
                    abort(400, "Der Bot benötigt die Berechtigung „Rollen verwalten“, damit Rollen-Buttons funktionieren.")
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
 document.querySelector("form").addEventListener("submit",()=>document.querySelectorAll(".manual-button-row[hidden]").forEach(row=>row.querySelectorAll("input,select").forEach(el=>el.disabled=true)));
 updateScnxPreview();
});
</script>"""
    success = '<div class="notice">✅ Nachricht wurde vom Bot gesendet.</div>' if request.args.get("sent") == "1" else ''
    body = '<section class="card scnx-message-builder"><span class="pill">MESSAGE BUILDER</span><h1>✉️ Nachricht erstellen</h1><p class="muted">Erstelle eine Discord-Nachricht wie in einem visuellen Editor: Inhalt links bearbeiten, Vorschau rechts ansehen und am Ende direkt senden.</p>' + success + error_html + '<form method="post">' + _csrf_field() + '<div class="scnx-builder"><div><section class="scnx-panel"><h2>1. Wo soll die Nachricht hin?</h2><label for="channel_id">Discord-Kanal</label><select id="channel_id" name="channel_id" required>' + channels + '</select><small class="scnx-field-help">Wähle den Kanal, in dem der Bot die Nachricht veröffentlichen soll.</small></section><section class="scnx-panel"><h2>2. Nachricht gestalten</h2><label for="message">Nachrichtentext</label><textarea id="message" name="message" maxlength="2000" rows="4" placeholder="Schreibe hier deine Nachricht …"></textarea><small class="scnx-field-help">Normale Discord-Nachricht, maximal 2000 Zeichen.</small><h3>Embed (optional)</h3><label for="embed_title">Überschrift</label><input id="embed_title" name="embed_title" maxlength="256" placeholder="z. B. Willkommen auf dem Server!"><label for="embed_description">Beschreibung</label><textarea id="embed_description" name="embed_description" maxlength="4000" rows="5" placeholder="Erkläre hier, was Mitglieder tun sollen …"></textarea><small class="scnx-field-help">Du kannst nur normalen Text, nur einen Embed oder beides zusammen senden.</small></section><section class="scnx-panel"><h2>3. Buttons hinzufügen</h2><p class="muted">Füge nur die Buttons hinzu, die du brauchst. Wähle einen Button-Typ; unnötige Felder werden automatisch ausgeblendet.</p>' + button_fields + '</section><button class="scnx-send" type="submit">🚀 Nachricht jetzt senden</button></div><aside class="scnx-panel scnx-preview"><div class="scnx-preview-label">Live-Vorschau · Discord</div><div class="scnx-discord-preview"><div class="scnx-preview-message" id="scnx-preview-message">Deine Nachricht erscheint hier …</div><div class="scnx-preview-embed" id="scnx-preview-embed" hidden><div class="scnx-preview-title" id="scnx-preview-title"></div><div class="scnx-preview-desc" id="scnx-preview-desc"></div></div><div class="scnx-preview-buttons" id="scnx-preview-buttons"></div></div><p class="muted" style="font-size:.85rem;margin-top:12px">Die Vorschau ist eine ungefähre Darstellung. Das tatsächliche Aussehen richtet sich nach Discord.</p></aside></div></form><p><a class="button secondary" href="/guild/' + gid + '">← Serverübersicht</a></p></section>'
    return _render(guild.name + " · Bot-Nachricht", body)


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
        _config[str(guild.id)] = backup
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Konfiguration wiederhergestellt", session.get("user_id"), "Serverkonfiguration aus JSON-Sicherung importiert")
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
        '<div class="notice"><small>Aktive Befehle</small><h2>' + str(sum(1 for item in commands if item["enabled"])) + '</h2></div>'
        '<div class="notice"><small>Erkannte Module</small><h2>' + str(len(modules)) + '</h2></div>'
        '<div class="notice"><small>Erfasste Nachrichten</small><h2>' + str(message_total) + '</h2></div>'
        '<div class="notice"><small>Mitglieder mit Aktivitätsdaten</small><h2>' + str(active_users) + '</h2></div>'
        '<div class="notice"><small>Erfasste Voice-Zeit</small><h2>' + str(voice_seconds // 3600) + ' Std.</h2></div></div>'
        '<p class="muted">Aktivitätswerte stammen aus gespeicherten Bot-Statistiken und sind keine Echtzeit-Zeitreihe.</p><a class="button secondary" href="/guild/' + str(guild.id) + '">← Serverübersicht</a></section>'
    )
    return _render(guild.name + " · Live-Statistiken", body)

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
    """Dedicated dashboard category for the server economy."""
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    cfg = _guild_config(guild.id)
    economy_cfg = cfg.setdefault("economy_dashboard", {})
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        actor = guild.get_member(int(session.get("user_id", "0")))
        if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
            abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
        currency_name = request.form.get("currency_name", "Münzen").strip()[:32] or "Münzen"
        economy_cfg["currency_name"] = currency_name
        economy_cfg["enabled"] = request.form.get("enabled") == "on"
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Wirtschaft & Shops", session.get("user_id"), "Änderung gespeichert")
        return redirect(url_for("guild_economy", guild_id=gid))
    enabled = economy_cfg.get("enabled", True)
    checked = " checked" if enabled else ""
    currency_name = html.escape(str(economy_cfg.get("currency_name", "Münzen")), quote=True)
    body = (
        '<section class="card"><h1>💰 Wirtschaftssystem</h1>'
        '<p>Verwalte hier die Wirtschaft deines Servers und finde die wichtigsten Geld-, Shop- und Saisonfunktionen an einem Ort.</p>'
        '<form method="post">' + _csrf_field() +
        '<label for="currency_name">Währungsname</label>'
        '<input id="currency_name" name="currency_name" maxlength="32" value="' + currency_name + '" required>'
        '<div class="check"><input type="checkbox" id="enabled" name="enabled"' + checked + '>'
        '<label for="enabled">Wirtschaftssystem im Dashboard als aktiviert markieren</label></div>'
        '<button type="submit">Einstellungen speichern</button></form>'
        '<p class="muted">Hinweis: Diese Anzeige-Einstellungen speichern die Dashboard-Konfiguration. Die vorhandenen Economy-Befehle und ihre Währung werden dadurch nicht automatisch umbenannt oder deaktiviert.</p></section>'
        '<div class="grid">'
        '<section class="card"><h2>🪙 Geld & Shop</h2><p>Die normalen Economy-Befehle sind im Bot verfügbar. Prüfe <code>/</code> für Slash-Befehle und nutze die Prefix-Befehle mit deinem Bot-Präfix.</p></section>'
        '</div><p><a href="/guild/' + gid + '">← Zur Serververwaltung</a></p>'
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
        + '<label>Beschreibung</label><textarea name="description" maxlength="4000">' + html.escape(str(cfg.get("verify_embed_description", "Lies die Serverregeln und klicke auf den Button, um dich zu verifizieren."))) + '</textarea>'
        + '<label>Button-Text</label><input name="button_label" maxlength="80" value="' + html.escape(str(cfg.get("verify_button_label", "Verifizieren")), quote=True) + '">'
        + '<label>Button-Emoji</label><input name="emoji" maxlength="100" value="' + html.escape(str(cfg.get("verify_button_emoji", "✅")), quote=True) + '">'
        + '<button type="submit">Verifizierung speichern und veröffentlichen</button></form><form method="post" onsubmit="return confirm(\'Verifizierungsnachricht wirklich löschen?\')">' + _csrf_field() + '<input type="hidden" name="action" value="delete_verification"><button type="submit" class="secondary">🗑️ Verifizierungsnachricht löschen</button></form></section>'
        + f'<p><a class="button secondary" href="/guild/{gid}">← Zur Serververwaltung</a></p>'
    )
    return _render(guild.name + " · Verifizierung", body)


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
        submitted_labels = [request.form.get(f"button{i}", "").strip() for i in range(1, 6)]
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
        labels = [request.form.get(f"button{i}", "").strip()[:80] for i in range(1, 6)]
        labels = [label for label in labels if label]
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
            abort(403, "Mehrere Ticket-Buttons sind Premium. Im Free-Plan ist ein Button pro Panel verfügbar.")
        if not isinstance(channel, discord.TextChannel):
            abort(400, "Bitte wähle einen gültigen Textkanal.")
        if category_id and not isinstance(category, discord.CategoryChannel):
            abort(400, "Bitte wähle eine gültige Ticket-Kategorie.")
        if support_role_id and support_role is None:
            abort(400, "Bitte wähle eine gültige Support-Rolle.")
        panel.update({
            "title": title,
            "description": description,
            "labels": labels,
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
            view = view_cls(panel_key, labels)
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
    body = (
        f'<span class="pill">IRONWOLF · TICKET CENTER</span><h1 style="margin-top:12px">🎟️ Ticket-System · {html.escape(guild.name)}</h1>'
        '<p class="muted">Bearbeite Titel, Beschreibung, Button-Texte, Zielkanal, Ticket-Kategorie und Support-Rolle. Gespeicherte Panel-Nachrichten werden nach Möglichkeit direkt bearbeitet.</p>'
        '<div class="notice"><strong>Free vs. Premium Ticket-System</strong><br>🆓 <b>Normal:</b> Tickets eröffnen, private Ticket-Kanäle, Support-Rolle, Claim/Freigeben und Schließen. Ein Transcript wird in den Logkanal hochgeladen, wenn er eingerichtet ist.<br>✨ <b>Premium Ticket-System:</b> mehrere konfigurierbare Ticket-Panels, bis zu fünf Anfrage-Buttons pro Panel, eigenes Panel-Branding (Farbe und Logo), Prioritätsmarkierung für wichtige Tickets und Transcript zusätzlich per DM an den Ticket-Ersteller. Premium-Funktionen benötigen einen verknüpften Premium-Plan oder eine aktive Testphase.</div>'
        + result_html
    )
    body += '<section class="card"><h2>＋ Neues Ticket-Panel erstellen</h2><p class="muted">Erstellt und veröffentlicht ein neues Ticket-Panel direkt aus dem Dashboard.</p><form method="post" enctype="multipart/form-data">' + _csrf_field() + '<input type="hidden" name="panel_id" value="__new__"><label>Titel</label><input name="title" maxlength="256" required value="Support-Ticket"><label>Beschreibung</label><textarea name="description" maxlength="4000">Wähle unten die passende Anfrage aus.</textarea><label>Button 1</label><input name="button1" maxlength="80" required value="Allgemeiner Support"><label>Button 2 (optional)</label><input name="button2" maxlength="80"><label>Button 3 (optional)</label><input name="button3" maxlength="80"><label>Button 4 (optional)</label><input name="button4" maxlength="80"><label>Button 5 (optional)</label><input name="button5" maxlength="80"><label>Panel-Kanal</label><select name="channel_id" required>' + options(channels, None, "— Textkanal auswählen —") + '</select><label>Ticket-Kategorie (optional)</label><select name="category_id">' + options(categories, None, "— Standard-Kategorie verwenden —") + '</select><label>Support-Rolle (optional)</label><select name="support_role_id">' + options(roles, None, "— Standard-Support-Rolle verwenden —") + '</select>' + ('<h3>✨ Custom Branding</h3><label>Embed-Farbe</label><div style="display:flex;gap:12px;align-items:center"><input type="color" name="brand_color_picker" value="#5865F2" oninput="this.form.brand_color.value=this.value.slice(1)" style="width:64px;height:48px;padding:3px"><input name="brand_color" maxlength="6" pattern="[0-9a-fA-F]{6}" value="5865F2" oninput="this.form.brand_color_picker.value=\'#\'+this.value"></div><label>Logo vom Computer hochladen (PNG, JPG, GIF, WEBP; max. 5 MB)</label><input type="file" name="brand_logo_file" accept="image/png,image/jpeg,image/gif,image/webp"><label>Alternativ Logo-URL (HTTPS, optional)</label><input name="brand_logo_url" type="url" maxlength="500"></div>' if guild_has_feature(cfg, "branding") else '') + '<button type="submit">Ticket-Panel erstellen und veröffentlichen</button></form></section>'
    if not panels:
        body += '<div class="notice">Für diesen Server sind noch keine Ticket-Panels gespeichert. Nutze das Formular „Neues Ticket-Panel erstellen“ oben, um eines direkt hier anzulegen.</div>'
    for panel_key, panel in sorted(panels.items(), key=lambda pair: pair[0]):
        labels = list(panel.get("labels", []))[:5]
        button_fields = ""
        for i in range(1, 6):
            value = labels[i-1] if len(labels) >= i else ""
            button_fields += f'<label>Button {i} ' + ('(erforderlich)' if i == 1 else '(optional)') + f'</label><input type="text" name="button{i}" maxlength="80" value="{html.escape(str(value), quote=True)}" placeholder="z. B. Allgemeiner Support">'
        body += (
            '<section class="card"><span class="pill">PANEL-ID ' + html.escape(str(panel_key)) + '</span>'
            '<form method="post" enctype="multipart/form-data">' + _csrf_field() + '<input type="hidden" name="panel_id" value="' + html.escape(str(panel_key), quote=True) + '">'
            '<label>Titel</label><input type="text" name="title" maxlength="256" required value="' + html.escape(str(panel.get("title", "Support-Ticket")), quote=True) + '">'
            '<label>Beschreibung</label><textarea name="description" maxlength="4000">' + html.escape(str(panel.get("description", ""))) + '</textarea>'
            + button_fields
            + '<label>Panel-Kanal</label><select name="channel_id" required>' + options(channels, panel.get("channel_id"), "— Textkanal auswählen —") + '</select>'
            + '<label>Ticket-Kategorie (optional)</label><select name="category_id">' + options(categories, panel.get("category_id"), "— Standard-Kategorie verwenden —") + '</select>'
            + '<label>Support-Rolle (optional)</label><select name="support_role_id">' + options(roles, panel.get("support_role_id"), "— Standard-Support-Rolle verwenden —") + '</select>'
            + ('<h3>✨ Custom Branding</h3><label>Embed-Farbe</label><div style="display:flex;gap:12px;align-items:center"><input type="color" name="brand_color_picker" value="#' + html.escape(str(panel.get("brand_color", "5865F2")).lstrip("#"), quote=True) + '" oninput="this.form.brand_color.value=this.value.slice(1)" style="width:64px;height:48px;padding:3px"><input name="brand_color" maxlength="6" pattern="[0-9a-fA-F]{6}" value="' + html.escape(str(panel.get("brand_color", "5865F2")).lstrip("#"), quote=True) + '" oninput="this.form.brand_color_picker.value=\'#\'+this.value" placeholder="5865F2"></div><label>Logo vom Computer hochladen (PNG, JPG, GIF, WEBP; max. 5 MB)</label><input type="file" name="brand_logo_file" accept="image/png,image/jpeg,image/gif,image/webp"><small>' + ('Aktuelles Logo: ' + html.escape(str(panel.get("brand_logo_filename", "hochgeladen")), quote=True) if panel.get("brand_logo_file") else 'Noch kein hochgeladenes Logo.') + '</small><label>Alternativ Logo-URL (HTTPS, optional)</label><input name="brand_logo_url" type="url" maxlength="500" value="' + html.escape(str(panel.get("brand_logo_url", "")), quote=True) + '"><div class="check"><input type="checkbox" name="remove_brand_logo" id="remove-logo-' + html.escape(str(panel_key), quote=True) + '"><label for="remove-logo-' + html.escape(str(panel_key), quote=True) + '">Hochgeladenes Logo entfernen</label></div>' if guild_has_feature(cfg, "branding") else '<h3>🔒 Premium-Funktion</h3><p class="muted">Custom Branding ist gesperrt. Verknüpfe Premium mit diesem Server, um Farbe und Logo bearbeiten zu können.</p><fieldset disabled style="opacity:.55"><label>Embed-Farbe</label><input value="5865F2"><label>Logo-URL</label><input value="Premium erforderlich"></fieldset>')
            + '<button type="submit">Panel speichern und veröffentlichen</button></form><form method="post" onsubmit="return confirm(\'Dieses Ticket-Panel und seine Nachricht wirklich löschen?\')">' + _csrf_field() + '<input type="hidden" name="panel_id" value="' + html.escape(str(panel_key), quote=True) + '"><input type="hidden" name="action" value="delete_panel"><button type="submit" class="secondary">🗑️ Panel und Nachricht löschen</button></form></section>'
        )
    body += f'<p><a class="button secondary" href="/guild/{gid}">← Zur Serververwaltung</a></p>'
    return _render(guild.name + " · Ticket-System", body)


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
            if request.form.get("cmd:" + key) == "on":
                disabled.discard(key)
            else:
                disabled.add(key)
        cfg["disabled_commands"] = sorted(disabled)
        _save_json(data_path("config.json"), _config)
        _audit_dashboard_change(guild.id, "Befehlsrechte", session.get("user_id"), "Deaktivierte Befehle: " + ", ".join(sorted(disabled))[:350])
        return redirect(url_for("guild_commands", guild_id=gid))
    commands = _command_records(guild.id)
    groups = {}
    for item in commands:
        groups.setdefault(item["cog"], []).append(item)
    command_html = '<span class="pill">IRONWOLF · COMMAND CENTER</span><h1 style="margin-top:12px">⌘ Befehle</h1><p class="muted">Alle Befehle sind nach Modul gruppiert. Schalte nur die Befehle ab, die auf diesem Server nicht verfügbar sein sollen.</p><div class="section-nav"><a href="/guild/' + gid + '">← Serverübersicht</a><a href="#prefix">Prefix-Befehle</a><a href="#slash">Slash-Befehle</a></div>'
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
    command_html += '</div><button type="submit">Änderungen speichern</button> <a class="button secondary" href="/guild/' + gid + '">Zurück zur Übersicht</a></form>'
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
    try:
        import voice_support_dashboard
        voice_support_dashboard.register()
    except Exception as exc:
        print(f"[DASHBOARD] Voice support settings route could not be registered: {type(exc).__name__}: {exc}", flush=True)
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
