"""Beispiel für discord.py + aiohttp.

In die vorhandene aiohttp-Dashboard-App integrieren und die Route /api/modules
öffentlich lesbar machen (keine Bot-Token, Userdaten oder Serverkonfiguration zurückgeben).
Die Website pollt diesen Endpoint jede Minute.
"""
from datetime import datetime, timezone
from aiohttp import web


def register_module_endpoint(app: web.Application, bot):
    async def modules(request: web.Request):
        entries = []
        # Jeder Cog wird als Modul ausgegeben. Neue Cogs erscheinen automatisch.
        for cog_name, cog in bot.cogs.items():
            commands = getattr(cog, "get_commands", lambda: [])()
            descriptions = []
            for command in commands:
                description = getattr(command, "brief", None) or getattr(command, "help", None)
                if description:
                    descriptions.append(str(description).splitlines()[0][:180])
            entries.append({
                "id": "".join(ch.lower() if ch.isalnum() else "-" for ch in cog_name).strip("-"),
                "name": cog_name,
                "description": (descriptions[0] if descriptions else f"{cog_name} — IronWolf-Modul."),
                "enabled": True,
            })
        standalone = [cmd for cmd in bot.tree.get_commands() if not getattr(cmd, "binding", None)]
        if standalone:
            entries.append({
                "id": "core-commands", "name": "Core-Befehle",
                "description": f"{len(standalone)} eigenständige Slash-Befehle.", "enabled": True,
            })
        return web.json_response({
            "name": "IronWolf",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "modules": entries,
        }, headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})

    app.router.add_get("/api/modules", modules)
