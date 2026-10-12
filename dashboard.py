"""Password-protected web dashboard for the Discord bot.
Set DASHBOARD_PASSWORD and expose Railway's PORT. Use HTTPS in production.
"""
import os
import json
import asyncio
import hmac
import hashlib
import html
from aiohttp import web


def start_dashboard(bot, config, save_json, guild_config):
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if not password:
        print("Dashboard disabled: set DASHBOARD_PASSWORD to enable secure login.")
        return
    if getattr(bot, "_dashboard_started", False):
        return
    bot._dashboard_started = True

    cookie_value = hashlib.sha256((password + "|KingSnowyDashboard-v1").encode()).hexdigest()
    app = web.Application(client_max_size=1024 * 1024)
    register_module_endpoint(app, bot)
    def authed(request):
        return hmac.compare_digest(request.cookies.get("ks_dashboard", ""), cookie_value)

    def page(body, message=""):
        return f'''<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>KingSnowy Bot Dashboard</title>
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#0b1020;color:#e8edf7;font:15px system-ui,Arial}}header{{padding:24px max(20px,calc((100vw - 1100px)/2));background:#121a2e;border-bottom:1px solid #293550}}main{{max-width:1100px;margin:24px auto;padding:0 16px}}h1{{margin:0;font-size:25px}}h2{{font-size:19px}}.muted{{color:#a9b5cc}}.card{{background:#121a2e;border:1px solid #293550;border-radius:14px;padding:18px;margin:16px 0}}input,select,textarea{{width:100%;padding:11px;border:1px solid #34425e;background:#0b1020;color:#fff;border-radius:8px;margin:6px 0 12px}}textarea{{min-height:280px;font:13px ui-monospace,monospace}}button,.button{{display:inline-block;padding:10px 14px;background:#647cf6;color:white;border:0;border-radius:8px;cursor:pointer;text-decoration:none;font-weight:650}}button.secondary{{background:#34425e}}.msg{{color:#a9f0c2;margin:10px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}.item{{display:flex;align-items:center;justify-content:space-between;gap:12px;background:#0b1020;border:1px solid #293550;border-radius:10px;padding:12px}}.item small{{display:block;color:#9caac3;overflow-wrap:anywhere}}.toggle{{width:auto;margin:0;transform:scale(1.25)}}.row{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}.row select{{max-width:420px}}.danger{{color:#ffb5b5}}@media(max-width:600px){{main{{padding:0 10px}}.card{{padding:13px}}}}
</style></head><body><header><h1>🐺 KingSnowy · Bot Dashboard</h1><div class="muted">Befehle, Server-Einstellungen und Module verwalten</div></header><main>{('<div class="msg">'+message+'</div>') if message else ''}{body}<p class="muted">Sicherheit: DASHBOARD_PASSWORD als Railway-Variable setzen. Teile das Passwort nicht und nutze die Website nur über HTTPS.</p></main></body></html>'''

    def login_form(error=""):
        return page(f'''<section class="card"><h2>Admin-Anmeldung</h2><p class="muted">Gib das Dashboard-Passwort ein, das du in Railway als <code>DASHBOARD_PASSWORD</code> gesetzt hast.</p><form method="post" action="/login"><label>Passwort</label><input type="password" name="password" required autofocus><button>Anmelden</button></form><p class="danger">{error}</p></section>''')

    def guild_options(selected):
        return ''.join(f'<option value="{g.id}" {"selected" if str(g.id)==str(selected) else ""}>{html.escape(g.name)} · {g.id}</option>' for g in bot.guilds)

    def command_list(guild_id):
        cfg = guild_config(int(guild_id))
        disabled = set(cfg.get("disabled_commands", []))
        items = []
        def add(name, desc, kind):
            key = f"{kind}:{name}"
            checked = "checked" if key not in disabled else ""
            items.append(f'''<label class="item"><span><strong>{html.escape(name)}</strong><small>{html.escape(desc or kind)}</small><small>{kind}</small></span><input class="toggle cmd-toggle" type="checkbox" data-key="{html.escape(key)}" {checked}></label>''')
        for c in bot.tree.get_commands():
            add('/' + c.qualified_name, getattr(c, 'description', '') or '', 'slash')
        for c in bot.commands:
            if c.hidden:
                continue
            add('!' + c.name, c.help or c.brief or '', 'prefix')
        items.sort(key=lambda x: x.casefold())
        return '<div class="grid">' + ''.join(items) + '</div>'

    async def login(request):
        form = await request.post()
        if hmac.compare_digest(str(form.get("password", "")), password):
            response = web.HTTPFound('/')
            response.set_cookie("ks_dashboard", cookie_value, httponly=True, secure=True, samesite="Strict", max_age=8*60*60)
            raise response
        return web.Response(text=login_form("Passwort falsch."), content_type="text/html", status=401)

    async def index(request):
        if not authed(request):
            return web.Response(text=login_form(), content_type="text/html")
        if not bot.guilds:
            return web.Response(text=page('<section class="card">Der Bot ist noch mit keinem Discord-Server verbunden.</section><a class="button" href="/logout">Abmelden</a>'), content_type="text/html")
        requested = request.query.get('guild') or str(bot.guilds[0].id)
        guild = bot.get_guild(int(requested))
        if guild is None:
            guild = bot.guilds[0]
        gid = str(guild.id)
        cfg = guild_config(guild.id)
        cfg_json = json.dumps(cfg, ensure_ascii=False, indent=2)
        body = f'''<div class="row"><form method="get" action="/" class="row"><label for="guild">Discord-Server</label><select id="guild" name="guild">{guild_options(gid)}</select><button class="secondary">Öffnen</button></form><a class="button" href="/logout">Abmelden</a></div>
<section class="card"><h2>Befehle aktivieren / deaktivieren</h2><p class="muted">Schalte einzelne Slash- und Prefix-Befehle für <strong>{html.escape(guild.name)}</strong> aus oder ein. Änderungen werden direkt gespeichert.</p><input id="search" placeholder="Befehl suchen …"><div class="row"><button type="button" id="all-on" class="secondary">Alle aktivieren</button><button type="button" id="all-off" class="secondary">Alle deaktivieren</button><span id="status" class="muted"></span></div><div id="commands">{command_list(gid)}</div></section>
<section class="card"><h2>Server-Konfiguration bearbeiten</h2><p class="muted">Hier kannst du die komplette Konfiguration dieses Servers als JSON bearbeiten, z. B. Kanal-IDs, Rollen-IDs, Begrüßungstext und Security-Einstellungen. Ungültiges JSON wird nicht gespeichert.</p><form method="post" action="/save-config"><input type="hidden" name="guild" value="{gid}"><textarea name="config" spellcheck="false">{html.escape(cfg_json)}</textarea><button>Konfiguration speichern</button></form></section>
<section class="card"><h2>Status</h2><p>Bot: <strong>{'Online' if bot.is_ready() else 'Verbindet …'}</strong></p><p>Server: {len(bot.guilds)} · Slash-Befehle: {len(bot.tree.get_commands())} · Prefix-Befehle: {sum(1 for c in bot.commands if not c.hidden)}</p><p class="muted">Befehle deaktivieren verhindert ihre Nutzung über Discord. Es löscht keine Daten und entfernt keine Nachrichten.</p></section>
<script>
const gid={json.dumps(gid)};const status=document.getElementById('status');
async function setCommand(key,enabled){{status.textContent='Speichere …';try{{let r=await fetch('/api/command',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{guild:gid,key:key,enabled:enabled}})}});let d=await r.json();status.textContent=r.ok?'Gespeichert':'Fehler: '+(d.error||'unbekannt')}}catch(e){{status.textContent='Verbindungsfehler'}}}}
document.querySelectorAll('.cmd-toggle').forEach(el=>el.addEventListener('change',()=>setCommand(el.dataset.key,el.checked)));
document.getElementById('search').addEventListener('input',e=>document.querySelectorAll('#commands .item').forEach(el=>el.style.display=el.innerText.toLowerCase().includes(e.target.value.toLowerCase())?'flex':'none'));
document.getElementById('all-on').addEventListener('click',()=>{{document.querySelectorAll('.cmd-toggle').forEach(el=>{{if(!el.checked){{el.checked=true;setCommand(el.dataset.key,true)}}}})}});
document.getElementById('all-off').addEventListener('click',()=>{{if(!confirm('Wirklich alle Befehle auf diesem Server deaktivieren?'))return;document.querySelectorAll('.cmd-toggle').forEach(el=>{{if(el.checked){{el.checked=false;setCommand(el.dataset.key,false)}}}})}});
</script>'''
        return web.Response(text=page(body), content_type="text/html")

    async def save_config(request):
        if not authed(request):
            return web.HTTPFound('/login')
        form = await request.post()
        try:
            gid = int(form.get('guild', ''))
            if bot.get_guild(gid) is None:
                raise ValueError('Unbekannter Server.')
            edited = json.loads(form.get('config', ''))
            if not isinstance(edited, dict):
                raise ValueError('Die Konfiguration muss ein JSON-Objekt sein.')
            # Keep the command control list unless explicitly included in the edited JSON.
            config[str(gid)] = edited
            guild_config(gid)
            save_json('config.json', config)
            return web.Response(text=page('<section class="card"><h2>Gespeichert</h2><p>Die Server-Konfiguration wurde gespeichert.</p><a class="button" href="/?guild='+str(gid)+'">Zurück zum Dashboard</a></section>'), content_type='text/html')
        except Exception as exc:
            return web.Response(text=page('<section class="card"><h2>Fehler</h2><p>'+html.escape(str(exc))+'</p><a class="button" href="/">Zurück</a></section>'), content_type='text/html', status=400)

    async def api_command(request):
        if not authed(request):
            return web.json_response({'error': 'Nicht angemeldet'}, status=401)
        try:
            payload = await request.json()
            gid = int(payload['guild'])
            key = str(payload['key'])
            if bot.get_guild(gid) is None:
                raise ValueError('Unbekannter Server')
            valid = set()
            for c in bot.tree.get_commands():
                valid.add('slash:/' + c.qualified_name)
            for c in bot.commands:
                if not c.hidden:
                    valid.add('prefix:!' + c.name)
            if key not in valid:
                raise ValueError('Unbekannter Befehl')
            cfg = guild_config(gid)
            disabled = set(cfg.get('disabled_commands', []))
            if payload.get('enabled'):
                disabled.discard(key)
            else:
                disabled.add(key)
            cfg['disabled_commands'] = sorted(disabled)
            save_json('config.json', config)
            return web.json_response({'ok': True})
        except Exception as exc:
            return web.json_response({'error': str(exc)}, status=400)

    async def logout(request):
        response = web.HTTPFound('/')
        response.del_cookie('ks_dashboard')
        raise response

    @web.middleware
    async def security_headers(request, handler):
        response = await handler(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    app.middlewares.append(security_headers)
    app.router.add_get('/', index)
    app.router.add_post('/login', login)
    app.router.add_post('/save-config', save_config)
    app.router.add_post('/api/command', api_command)
    app.router.add_get('/logout', logout)

    async def runner_start():
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv('PORT', '8080'))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f'Admin dashboard listening on 0.0.0.0:{port}')
        bot._dashboard_runner = runner

    async def runner_start_guarded():
        try:
            await runner_start()
        except Exception as exc:
            bot._dashboard_started = False
            print(f'Could not start dashboard web server: {type(exc).__name__}: {exc}', flush=True)

    asyncio.get_running_loop().create_task(runner_start_guarded())

"""Beispiel für discord.py + aiohttp.

In die vorhandene aiohttp-Dashboard-App integrieren und die Route /api/modules
öffentlich lesbar machen (keine Bot-Token, Userdaten oder Serverkonfiguration zurückgeben).
Die Website pollt diesen Endpoint jede Minute.
"""
from datetime import datetime, timezone
from aiohttp import web



from datetime import datetime, timezone
from aiohttp import web
import re


def register_module_endpoint(app: web.Application, bot):

    MODULES = {
        "moderation": {
            "name": "Moderation",
            "description": "Moderationsbefehle und Serververwaltung.",
            "keywords": (
                "ban", "kick", "timeout", "mute", "unmute",
                "warn", "purge", "clear", "moderation",
                "member", "user", "slowmode"
            ),
        },
        "tickets": {
            "name": "Tickets",
            "description": "Ticket-System und Support-Einstellungen.",
            "keywords": (
                "ticket", "support", "claim", "close-ticket"
            ),
        },
        "anti-raid": {
            "name": "Anti-Raid",
            "description": "Schutz vor Join-Raids und verdächtigen Beitritten.",
            "keywords": (
                "raid", "honeypot", "security-toggle"
            ),
        },
        "anti-nuke": {
            "name": "Anti-Nuke",
            "description": "Schutz vor schädlichen Serveränderungen.",
            "keywords": (
                "antinuke", "anti-nuke", "trusted-role",
                "security-set-log", "security-set-antinuke"
            ),
        },
        "core-commands": {
            "name": "Core-Befehle",
            "description": "Allgemeine IronWolf-Befehle.",
            "keywords": (),
        },
    }

    def classify(command_name, description):
        text = f"{command_name} {description}".casefold()
        normalized = re.sub(r"[^a-z0-9]+", "-", text)

        # Sicherheitsbefehle zuerst zuordnen, damit sie nicht
        # versehentlich als gewöhnliche Moderation erscheinen.
        if any(k in normalized for k in (
            "antinuke", "anti-nuke", "trusted-role",
            "security-set-antinuke"
        )):
            return "anti-nuke"

        if any(k in normalized for k in (
            "raid", "honeypot", "security-toggle"
        )):
            return "anti-raid"

        if any(k in normalized for k in (
            "ticket", "support", "claim", "close-ticket"
        )):
            return "tickets"

        if any(k in normalized for k in MODULES["moderation"]["keywords"]):
            return "moderation"

        return "core-commands"

    async def modules(request: web.Request):
        grouped = {key: [] for key in MODULES}

        # Slash-Befehle einschließlich Unterbefehlen erkennen.
        for command in bot.tree.get_commands():
            commands = (
                command.walk_commands()
                if hasattr(command, "walk_commands")
                else [command]
            )

            for item in commands:
                name = getattr(item, "qualified_name", item.name)
                description = (
                    getattr(item, "description", "")
                    or getattr(item, "brief", "")
                    or ""
                )

                category = classify(name, description)

                grouped[category].append({
                    "name": name,
                    "description": str(description)[:180],
                    "type": "slash",
                })

        # Prefix-Befehle wie !stats ebenfalls erfassen.
        for command in bot.walk_commands():
            if getattr(command, "hidden", False):
                continue

            name = getattr(command, "qualified_name", command.name)
            description = getattr(command, "help", None) or ""
            category = classify(name, description)

            grouped[category].append({
                "name": "!" + name,
                "description": str(description).splitlines()[0][:180],
                "type": "prefix",
            })

        entries = []

        for module_id, info in MODULES.items():
            commands = grouped[module_id]

            entries.append({
                "id": module_id,
                "name": info["name"],
                "description": info["description"],
                "enabled": True,
                "detected": bool(commands),
                "commandCount": len(commands),
                "commands": commands,
            })

        return web.json_response(
            {
                "name": "IronWolf",
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "modules": entries,
                "totalCommands": sum(
                    len(commands) for commands in grouped.values()
                ),
            },
            headers={
                "Cache-Control": "no-store",
                "Access-Control-Allow-Origin": "*",
            },
        )

    app.router.add_get("/api/modules", modules)
