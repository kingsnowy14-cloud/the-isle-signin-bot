"""Server-scoped image generation with monthly server and account token pools.

Provider: Cloudflare Workers AI (FLUX.1 schnell). Configure CF_ACCOUNT_ID and
CF_API_TOKEN in the hosting environment. The token ledger is stored on DATA_DIR.
"""
import base64
import binascii
import html
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Blueprint, abort, redirect, request, session, url_for
from premium import account_plan
from storage import data_path

bp = Blueprint("image_generation", __name__)
_LEDGER = data_path("image_generation_tokens.json")
_LOCK = threading.RLock()
_OUTPUT_DIR = data_path("image_outputs")
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_MONTHLY_SERVER_TOKENS = 200
_MONTHLY_PREMIUM_TOKENS = 3000
_COSTS = {"512": 10, "768": 18, "1024": 30}
_MAX_PROMPT = 1200
_authorized_guild = None
_csrf_ok = None
_csrf_field = None


def register(app, authorized_guild, csrf_ok, csrf_field):
    global _authorized_guild, _csrf_ok, _csrf_field
    _authorized_guild, _csrf_ok, _csrf_field = authorized_guild, csrf_ok, csrf_field
    app.register_blueprint(bp)


def _month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load():
    try:
        data = json.loads(_LEDGER.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"servers": {}, "accounts": {}}
    except (OSError, json.JSONDecodeError):
        return {"servers": {}, "accounts": {}}


def _save(data):
    temp = _LEDGER.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, _LEDGER)


def _balances(guild_id, user_id):
    month = _month()
    with _LOCK:
        data = _load()
        server = data.setdefault("servers", {}).get(str(guild_id), {})
        if not isinstance(server, dict) or server.get("month") != month:
            server = {"month": month, "used": 0}
            data["servers"][str(guild_id)] = server
        account = data.setdefault("accounts", {}).get(str(user_id), {})
        if not isinstance(account, dict) or account.get("month") != month:
            account = {"month": month, "used": 0}
            data["accounts"][str(user_id)] = account
        _save(data)
        premium = account_plan(user_id) != "free"
        server_left = max(0, _MONTHLY_SERVER_TOKENS - int(server.get("used", 0)))
        personal_left = max(0, _MONTHLY_PREMIUM_TOKENS - int(account.get("used", 0))) if premium else 0
        return server_left, personal_left, premium


def _charge(guild_id, user_id, cost):
    """Atomically deduct server tokens first, then the user's premium pool."""
    month = _month()
    with _LOCK:
        data = _load()
        servers = data.setdefault("servers", {})
        accounts = data.setdefault("accounts", {})
        server = servers.get(str(guild_id), {})
        if not isinstance(server, dict) or server.get("month") != month:
            server = {"month": month, "used": 0}
        account = accounts.get(str(user_id), {})
        if not isinstance(account, dict) or account.get("month") != month:
            account = {"month": month, "used": 0}
        premium = account_plan(user_id) != "free"
        server_left = max(0, _MONTHLY_SERVER_TOKENS - int(server.get("used", 0)))
        personal_left = max(0, _MONTHLY_PREMIUM_TOKENS - int(account.get("used", 0))) if premium else 0
        if server_left + personal_left < cost:
            return False
        from_server = min(server_left, cost)
        from_personal = cost - from_server
        server["used"] = int(server.get("used", 0)) + from_server
        account["used"] = int(account.get("used", 0)) + from_personal
        servers[str(guild_id)] = server
        accounts[str(user_id)] = account
        _save(data)
        return True


def _generate(prompt, size):
    account_id = (os.getenv("CF_ACCOUNT_ID") or "").strip()
    api_token = (os.getenv("CF_API_TOKEN") or "").strip()
    if not account_id or not api_token:
        raise RuntimeError("Bild-KI ist noch nicht eingerichtet. Bitte CF_ACCOUNT_ID und CF_API_TOKEN in Railway setzen.")
    try:
        response = requests.post(
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell",
            headers={"Authorization": "Bearer " + api_token, "Content-Type": "application/json"},
            json={"prompt": prompt},
            timeout=180,
        )
    except requests.Timeout as exc:
        raise RuntimeError("Die Bildgenerierung hat zu lange gedauert. Bitte versuche es erneut.") from exc
    except requests.RequestException as exc:
        raise RuntimeError("Cloudflare ist nicht erreichbar. Prüfe die Railway-Netzwerkverbindung und versuche es erneut.") from exc

    if response.status_code in (401, 403):
        raise RuntimeError("Cloudflare lehnt den API-Schlüssel ab (401/403). Prüfe CF_API_TOKEN und die Berechtigung „Workers AI – Read“.")
    if response.status_code == 404:
        raise RuntimeError("Cloudflare findet das Workers-AI-Modell oder Konto nicht (404). Prüfe CF_ACCOUNT_ID und den Modell-Endpunkt.")
    if response.status_code == 429:
        raise RuntimeError("Das Cloudflare-Kontingent oder Rate-Limit ist erreicht. Bitte später erneut versuchen.")
    if not response.ok:
        detail = (response.text or "")[:500].replace("\\n", " ")
        print(f"[IMAGE GEN] Cloudflare HTTP {response.status_code}: {detail}", flush=True)
        raise RuntimeError(f"Cloudflare-Fehler HTTP {response.status_code}. Prüfe API-Token, Account-ID und Workers-AI-Kontingent.")

    content_type = (response.headers.get("content-type") or "").lower()
    if "image/" in content_type or response.content.startswith(b"\x89PNG\r\n\x1a\n"):
        image_bytes = response.content
    else:
        try:
            payload = response.json()
        except ValueError as exc:
            detail = (response.text or "")[:300]
            print(f"[IMAGE GEN] Unexpected response ({content_type}): {detail}", flush=True)
            raise RuntimeError("Cloudflare hat kein Bildformat zurückgegeben. Prüfe Modell und Workers-AI-Konfiguration.") from exc
        if payload.get("success") is False:
            errors = payload.get("errors") or []
            detail = "; ".join(str(item.get("message", item)) for item in errors)[:400]
            print(f"[IMAGE GEN] Cloudflare API error: {detail}", flush=True)
            raise RuntimeError("Cloudflare meldet einen Fehler: " + (detail or "unbekannter API-Fehler"))
        result = payload.get("result") or {}
        raw = result.get("image")
        if not isinstance(raw, str) or not raw:
            raise RuntimeError("Cloudflare hat erfolgreich geantwortet, aber kein Bild geliefert.")
        try:
            image_bytes = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RuntimeError("Die Bildantwort von Cloudflare war ungültig.") from exc

    if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Cloudflare hat kein gültiges PNG-Bild geliefert.")
    return image_bytes


@bp.route("/guild/<guild_id>/image-generator", methods=["GET", "POST"])
def image_generator(guild_id):
    guild, response = _authorized_guild(guild_id)
    if response:
        return response
    user_id = str(session.get("user_id") or "")
    actor = guild.get_member(int(user_id)) if user_id.isdigit() else None
    if actor is None or not (actor.guild_permissions.administrator or actor.guild_permissions.manage_guild):
        abort(403, "Du brauchst Administrator- oder Server-verwalten-Rechte.")
    message = ""
    image_url = ""
    if request.method == "POST":
        if not _csrf_ok():
            abort(400, "CSRF-Prüfung fehlgeschlagen. Bitte Seite neu laden.")
        prompt = request.form.get("prompt", "").strip()
        size = request.form.get("size", "512")
        if not prompt or len(prompt) > _MAX_PROMPT:
            abort(400, "Bitte gib einen Prompt mit maximal 1.200 Zeichen ein.")
        if size not in _COSTS:
            abort(400, "Ungültige Bildgröße.")
        cost = _COSTS[size]
        server_left, personal_left, premium = _balances(guild.id, user_id)
        if server_left + personal_left < cost:
            message = "Nicht genügend Bild-KI-Tokens. Das Serverkontingent und dein persönliches Premium-Kontingent werden getrennt verwaltet."
        else:
            try:
                image_bytes = _generate(prompt, size)
                filename = secrets.token_urlsafe(24) + ".png"
                target = _OUTPUT_DIR / filename
                target.write_bytes(image_bytes)
                # Charge only after successful generation and durable image storage.
                if not _charge(guild.id, user_id, cost):
                    target.unlink(missing_ok=True)
                    abort(409, "Dein Token-Guthaben hat sich geändert. Bitte versuche es erneut.")
                image_url = url_for("image_generation.image_file", filename=filename)
                message = f"Bild erfolgreich erstellt. Abgezogen: {cost} Tokens."
            except RuntimeError as exc:
                message = str(exc)
                print(f"[IMAGE GEN] {type(exc).__name__}: {exc}", flush=True)
            except requests.RequestException as exc:
                message = "Die Verbindung zum Bildanbieter ist fehlgeschlagen. Es wurden keine Tokens abgezogen. Bitte Railway-Logs prüfen."
                print(f"[IMAGE GEN] Provider request failed: {type(exc).__name__}: {exc}", flush=True)
    server_left, personal_left, premium = _balances(guild.id, user_id)
    total = server_left + personal_left
    premium_line = f'<div class="dashboard-metric"><strong>{personal_left:,}</strong><span>Persönliche Premium-Tokens · zusätzlich</span></div>' if premium else ""
    result = f'<div class="notice">{html.escape(message)}</div>' if message else ""
    image = (
        f'<section class="card"><h2>Dein Bild</h2><img src="{html.escape(image_url, quote=True)}" alt="KI-generiertes Bild" style="display:block;width:100%;max-width:760px;border-radius:14px;border:1px solid var(--border)"><p><a class="button" href="{html.escape(image_url, quote=True)}" download="ironwolf-ai-image.png">⬇ Bild herunterladen</a></p></section>'
        if image_url else ""
    )
    body = (
        '<span class="pill">IRONWOLF · IMAGE AI</span><h1 style="margin-top:12px">🎨 KI-Bildgenerator</h1>'
        '<p class="muted">Erstelle Bilder direkt für deinen Discord-Server. Tokens gelten ausschließlich für Bildgenerierung und werden monatlich zurückgesetzt; nicht genutzte Tokens verfallen.</p>'
        '<div class="dashboard-metrics">'
        f'<div class="dashboard-metric"><strong>{server_left:,} / 200</strong><span>Server-Tokens · nur dieser Server</span></div>'
        + premium_line +
        f'<div class="dashboard-metric"><strong>{total:,}</strong><span>Verfügbar für dieses Bild</span></div></div>'
        + result + image +
        '<section class="card"><h2>Neues Bild erstellen</h2><form method="post">' + _csrf_field()
        + '<label for="prompt">Bildbeschreibung</label><textarea id="prompt" name="prompt" maxlength="1200" required placeholder="z. B. Ein majestätischer silberner Wolf vor einer futuristischen Neon-Stadt ..."></textarea>'
        + '<label for="size">Bildgröße und Tokenpreis</label><select id="size" name="size"><option value="512">512 × 512 · 10 Tokens</option><option value="768">768 × 768 · 18 Tokens</option><option value="1024">1024 × 1024 · 30 Tokens</option></select>'
        + '<p class="muted" id="price-preview">Preis: 10 Tokens. Abrechnung erfolgt erst nach erfolgreicher Bildgenerierung.</p>'
        + '<button type="submit">✨ Bild generieren</button></form></section>'
        + '<p><a class="button secondary" href="/guild/' + str(guild.id) + '">Zur Serververwaltung</a></p>'
        + '<script>const s=document.getElementById("size"),p=document.getElementById("price-preview");s.addEventListener("change",()=>{p.textContent="Preis: "+({"512":10,"768":18,"1024":30}[s.value])+" Tokens. Abrechnung erfolgt erst nach erfolgreicher Bildgenerierung.";});</script>'
    )
    from flask import current_app
    return current_app.view_functions["dashboard_render"](guild.name + " · KI-Bildgenerator", body) if "dashboard_render" in current_app.view_functions else _standalone_page(guild.name, body)


@bp.route("/image-output/<filename>")
def image_file(filename):
    if not session.get("access_token") or not re.fullmatch(r"[A-Za-z0-9_-]{20,80}\.png", filename):
        abort(404)
    from flask import send_file
    path = _OUTPUT_DIR / filename
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype="image/png", as_attachment=False, max_age=0)


def _standalone_page(server_name, body):
    from flask import make_response
    return make_response(
        '<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>KI-Bildgenerator · IronWolf</title><body style="background:#080b12;color:#f2f5fb;font:16px system-ui;max-width:900px;margin:40px auto;padding:20px">'
        '<h1>IronWolf · ' + html.escape(server_name) + '</h1>' + body + '</body></html>'
    )
