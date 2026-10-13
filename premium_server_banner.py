"""Owner-managed server Premium allowlist and Premium server-banner uploads."""
import asyncio
import html
import io
import json
import os
from pathlib import Path

import discord
from flask import Blueprint, abort, redirect, request, session, url_for
from PIL import Image, UnidentifiedImageError
from storage import data_path

premium_banner_bp = Blueprint("premium_server_banner", __name__)
_ALLOWED_IMAGE_TYPES = {"PNG", "JPEG", "GIF"}
_PREMIUM_GUILDS_FILE = data_path("premium_guilds.json")
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_bot = None
_authorized_guild = None
_csrf_ok = None
_csrf_field = None
_render = None
_guild_config = None


def _load_premium_guilds():
    try:
        raw = json.loads(_PREMIUM_GUILDS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return {str(item) for item in raw if str(item).isdigit()}
    except (OSError, json.JSONDecodeError):
        pass
    return set()


def _save_premium_guilds(ids):
    _PREMIUM_GUILDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PREMIUM_GUILDS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(set(ids)), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, _PREMIUM_GUILDS_FILE)


def _is_dashboard_owner(user_id):
    owners = {
        item.strip()
        for item in (os.getenv("DASHBOARD_ADMIN_USER_IDS") or "").split(",")
        if item.strip().isdigit()
    }
    return bool(str(user_id).isdigit() and str(user_id) in owners)


def register_premium_banner_routes(app, bot, authorized_guild, csrf_ok, csrf_field, render, guild_config):
    global _bot, _authorized_guild, _csrf_ok, _csrf_field, _render, _guild_config
    _bot, _authorized_guild, _csrf_ok, _csrf_field, _render, _guild_config = (
        bot, authorized_guild, csrf_ok, csrf_field, render, guild_config
    )
    if premium_banner_bp.name not in app.blueprints:
        app.register_blueprint(premium_banner_bp)


@premium_banner_bp.route("/admin/premium-servers", methods=["GET", "POST"])
def premium_servers_admin():
    if not session.get("access_token"):
        return redirect(url_for("login"))
    if not _is_dashboard_owner(session.get("user_id", "")):
        abort(403, "Nur der Bot-Besitzer (DASHBOARD_ADMIN_USER_IDS) darf Premium-Server auswählen.")
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte lade die Seite neu.")
        submitted = {
            item for item in request.form.getlist("guild_ids")
            if item.isdigit() and _bot.get_guild(int(item)) is not None
        }
        _save_premium_guilds(submitted)
        return redirect(url_for("premium_server_banner.premium_servers_admin"))

    selected = _load_premium_guilds()
    rows = []
    for guild in sorted(_bot.guilds, key=lambda item: item.name.lower()):
        checked = " checked" if str(guild.id) in selected else ""
        rows.append(
            '<label style="display:flex;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid var(--border);">'
            f'<input type="checkbox" name="guild_ids" value="{guild.id}"{checked} style="width:18px;height:18px;accent-color:var(--accent)">'
            f'<span><strong>{html.escape(guild.name)}</strong><br><small>Server-ID: {guild.id}</small></span></label>'
        )
    if not rows:
        rows.append('<p class="muted">Der Bot ist aktuell auf keinem Server.</p>')
    body = (
        '<section class="card"><span class="pill">OWNER · PREMIUM</span><h1 style="margin-top:12px">Premium-Server manuell verwalten</h1>'
        '<p>Wähle aus, welche Server die Premium-Serverbanner-Funktion verwenden dürfen. Diese Freischaltung ist unabhängig von Nutzer-Premium und wird in Railway/Storage gespeichert.</p>'
        '<div class="notice">Nur dein Dashboard-Konto, das in <code>DASHBOARD_ADMIN_USER_IDS</code> eingetragen ist, kann diese Liste ändern.</div>'
        '<form method="post">' + _csrf_field() + ''.join(rows)
        + '<button type="submit">Premium-Server speichern</button></form>'
        '<p><a class="button secondary" href="/">Zurück zum Dashboard</a></p></section>'
    )
    return _render("Premium-Server verwalten", body)


@premium_banner_bp.route("/guild/<guild_id>/banner", methods=["GET", "POST"])
def guild_server_banner(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    gid = str(guild.id)
    if gid not in _load_premium_guilds():
        abort(403, "Dieser Server wurde noch nicht manuell für Premium-Serverbanner freigeschaltet.")
    actor_id = str(session.get("user_id", ""))
    actor = guild.get_member(int(actor_id)) if actor_id.isdigit() else None
    if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
        abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_guild:
        abort(403, "Der Bot benötigt die Discord-Berechtigung „Server verwalten“.")
    if "BANNER" not in guild.features:
        abort(400, "Discord hat für diesen Server das Serverbanner-Feature nicht freigeschaltet. IronWolf-Premium kann Discords Server-Boost-Voraussetzungen nicht umgehen.")

    result_html = ""
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte lade die Seite neu.")
        upload = request.files.get("banner")
        if upload is None or not upload.filename:
            abort(400, "Bitte wähle ein Bild aus.")
        raw = upload.read(_MAX_IMAGE_BYTES + 1)
        if not raw or len(raw) > _MAX_IMAGE_BYTES:
            abort(400, "Das Bild darf maximal 8 MB groß sein.")
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image_format = (image.format or "").upper()
                image.verify()
            if image_format not in _ALLOWED_IMAGE_TYPES:
                abort(400, "Erlaubte Bildformate: PNG, JPEG oder GIF.")
            if image_format == "GIF" and "ANIMATED_BANNER" not in guild.features:
                abort(400, "Animierte GIF-Banner sind für diesen Server nicht freigeschaltet. Lade bitte ein statisches PNG/JPEG/WEBP-Bild hoch.")
        except (UnidentifiedImageError, OSError, ValueError):
            abort(400, "Die hochgeladene Datei ist kein gültiges Bild.")
        try:
            future = asyncio.run_coroutine_threadsafe(
                guild.edit(banner=raw, reason=f"IronWolf Premium-Serverbanner geändert durch {actor} ({actor.id})"),
                _bot.loop,
            )
            future.result(timeout=30)
            result_html = '<div class="notice">✅ Das Serverbanner wurde erfolgreich geändert.</div>'
        except discord.Forbidden:
            result_html = '<div class="notice">⚠️ Discord hat die Änderung verweigert. Prüfe die Bot-Berechtigung „Server verwalten“.</div>'
        except (discord.HTTPException, asyncio.TimeoutError, ValueError, TypeError) as exc:
            result_html = '<div class="notice">⚠️ Discord konnte das Banner nicht ändern: ' + html.escape((str(exc) or type(exc).__name__)[:350]) + '</div>'

    current_banner = ""
    if guild.banner:
        current_banner = f'<p><strong>Aktuelles Banner</strong></p><img src="{html.escape(guild.banner.url)}" alt="Aktuelles Serverbanner" style="max-width:100%;border-radius:12px;border:1px solid var(--border)">'
    body = (
        '<section class="card"><span class="pill">PREMIUM · SERVER-BANNER</span>'
        f'<h1 style="margin-top:12px">Serverbanner ändern · {html.escape(guild.name)}</h1>'
        '<p>Das Banner wird für den gesamten Discord-Server geändert. Discord muss die Banner-Funktion für diesen Server freigeschaltet haben.</p>'
        + result_html + current_banner
        + '<div class="notice">Empfohlen: 960 × 540 Pixel (16:9). Maximal 8 MB. Discord kann zusätzliche Format- oder Größenanforderungen durchsetzen.</div>'
        + '<form method="post" enctype="multipart/form-data">' + _csrf_field()
        + '<label for="banner">Neues Banner (PNG, JPEG, GIF oder WEBP)</label>'
        + '<input id="banner" type="file" name="banner" accept="image/png,image/jpeg,image/gif" required>'
        + '<button type="submit">Serverbanner anwenden</button></form>'
        + f'<p><a class="button secondary" href="/guild/{gid}">Zur Serververwaltung</a></p></section>'
    )
    return _render(guild.name + " · Serverbanner", body)
