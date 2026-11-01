"""One-time IronWolf main-server announcement to every eligible guild."""
import asyncio
import json
import os
from pathlib import Path

import discord

from storage import data_path

EXCLUDED_GUILD_IDS = {1561027077984227371}
LEDGER_PATH = data_path("ironwolf_mainserver_announcement_v1.json")
_LOCK = asyncio.Lock()

ANNOUNCEMENT = """# 🐺 Willkommen bei IronWolf – Unser neuer Discord-Mainserver!

Hallo liebe IronWolf-Nutzer,

wir freuen uns, euch mitzuteilen, dass IronWolf ab sofort einen neuen **Discord-Mainserver** hat! Ihr seid herzlich eingeladen, unserem neuen Server beizutreten und Teil unserer Community zu werden.

👉 **Hier geht’s zum neuen Discord-Mainserver:**
https://discord.gg/KDKs5WPgaC

**💬 Hilfe, Support & Bugs**

Solltet ihr Fragen oder Probleme mit IronWolf haben oder Fehler (Bugs) entdecken, zögert bitte nicht, uns Bescheid zu geben. Erstellt bei Bedarf einfach ein Ticket – unser Team hilft euch gerne weiter!

**🤝 Unser Angebot für andere Server**

Ihr betreibt selbst einen Discord-Server? Dann haben wir ein Angebot für euch:

Veröffentlicht unseren IronWolf-Servertext in einem eigenen Kanal oder Forum auf eurem Server. Im Gegenzug stellen wir euren Server ebenfalls in unserem Forum-Kanal vor!

Wir freuen uns auf euren Besuch und auf eine gute Zusammenarbeit!

**Euer IronWolf-Team 🐺**"""


def _read_ledger():
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_ledger(data):
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = LEDGER_PATH.with_suffix(LEDGER_PATH.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, LEDGER_PATH)


def _announcement_channel(guild, bot_member):
    system = guild.system_channel
    candidates = []
    if isinstance(system, discord.TextChannel):
        candidates.append(system)

    text_channels = list(guild.text_channels)
    preferred_names = {"general", "chat", "willkommen", "welcome", "community"}
    text_channels.sort(key=lambda channel: (
        0 if channel.name.casefold() in preferred_names else 1,
        channel.position,
    ))
    candidates.extend(channel for channel in text_channels if channel not in candidates)

    for channel in candidates:
        permissions = channel.permissions_for(bot_member)
        if permissions.view_channel and permissions.send_messages:
            return channel
    return None


async def send_mainserver_announcement(bot):
    """Send once per guild; excluded guilds and inaccessible channels are skipped."""
    async with _LOCK:
        ledger = _read_ledger()
        sent = ledger.setdefault("sent_guild_ids", {})
        sent_count = 0
        skipped_count = 0

        for guild in list(bot.guilds):
            guild_key = str(guild.id)
            if guild.id in EXCLUDED_GUILD_IDS or guild_key in sent:
                skipped_count += 1
                continue

            bot_member = guild.get_member(bot.user.id) if bot.user else None
            if bot_member is None:
                try:
                    bot_member = await guild.fetch_member(bot.user.id)
                except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                    print(f"[MAINSERVER ANNOUNCEMENT] Cannot resolve bot member in {guild.id}: {type(exc).__name__}", flush=True)
                    continue

            channel = _announcement_channel(guild, bot_member)
            if channel is None:
                print(f"[MAINSERVER ANNOUNCEMENT] No writable text channel in guild {guild.id}; will retry later.", flush=True)
                continue

            try:
                message = await channel.send(
                    ANNOUNCEMENT,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[MAINSERVER ANNOUNCEMENT] Failed in guild {guild.id} / channel {channel.id}: {type(exc).__name__}: {exc}", flush=True)
                continue

            sent[guild_key] = {
                "channel_id": channel.id,
                "message_id": message.id,
                "sent_at": message.created_at.isoformat(),
            }
            _write_ledger(ledger)
            sent_count += 1
            print(f"[MAINSERVER ANNOUNCEMENT] Sent to guild {guild.id} ({guild.name}) in #{channel.name}; message {message.id}.", flush=True)

        print(f"[MAINSERVER ANNOUNCEMENT] Finished pass: {sent_count} sent, {skipped_count} skipped/already sent; remaining guilds will retry on next ready event.", flush=True)
