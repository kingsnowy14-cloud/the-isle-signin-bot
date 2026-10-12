IronWolf Economy Module
Für discord.py 2.x

Einbindung in deine bestehende Bot-Datei:
1. Installieren: pip install -U discord.py
2. Diese Datei neben bot.py/main.py speichern.
3. In bot.py nach den Imports ergänzen:
       from ironwolf_economy import setup_economy
4. Vor bot.run(TOKEN) ergänzen:
       setup_economy(bot)

Hinweis: Für Präfix-Befehle wie .work muss dein Bot commands.Bot mit
command_prefix="." verwenden. Slash-Befehle müssen beim Start synchronisiert
werden (siehe Hinweis am Ende).
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
import time
from pathlib import Path
from storage import data_path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

DB_PATH = data_path("ironwolf_economy.sqlite3")

DEFAULTS = {
    "currency_name": "Hamburg-Dollar",
    "currency_emoji": "💶",
    "starting_balance": "500",
    "work_min": "100",
    "work_max": "300",
    "work_cooldown": "3600",
    "crime_min": "150",
    "crime_max": "600",
    "crime_cooldown": "1800",
    "crime_success_percent": "55",
    "police_catch_percent": "35",
    "crime_fine_percent": "20",
}


class EconomyStore:
    """SQLite storage; each operation uses a short-lived connection."""

    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self.lock = asyncio.Lock()
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (guild_id, key)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
                    PRIMARY KEY (guild_id, user_id)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    actor_id INTEGER,
                    target_id INTEGER,
                    amount INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS cooldowns (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    command TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, command)
                )
            """)

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def setting(self, guild_id: int, key: str) -> str:
        with self._connect() as con:
            row = con.execute(
                "SELECT value FROM settings WHERE guild_id=? AND key=?",
                (guild_id, key),
            ).fetchone()
        return row["value"] if row else DEFAULTS[key]

    def settings(self, guild_id: int) -> dict[str, str]:
        result = dict(DEFAULTS)
        with self._connect() as con:
            rows = con.execute(
                "SELECT key, value FROM settings WHERE guild_id=?", (guild_id,)
            ).fetchall()
        result.update({r["key"]: r["value"] for r in rows})
        return result

    async def set_settings(self, guild_id: int, values: dict[str, str]):
        async with self.lock:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                con.executemany(
                    """INSERT INTO settings(guild_id,key,value) VALUES(?,?,?)
                       ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value""",
                    [(guild_id, k, v) for k, v in values.items()],
                )
                con.commit()

    async def ensure_account(self, guild_id: int, user_id: int) -> int:
        async with self.lock:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute(
                    "SELECT balance FROM accounts WHERE guild_id=? AND user_id=?",
                    (guild_id, user_id),
                ).fetchone()
                if row is None:
                    start = int(self.setting(guild_id, "starting_balance"))
                    con.execute(
                        "INSERT INTO accounts(guild_id,user_id,balance) VALUES(?,?,?)",
                        (guild_id, user_id, start),
                    )
                    if start:
                        con.execute(
                            """INSERT INTO transactions
                               (guild_id,actor_id,target_id,amount,kind,note,created_at)
                               VALUES(?,?,?,?,?,?,?)""",
                            (guild_id, None, user_id, start, "start",
                             "Startguthaben", int(time.time())),
                        )
                    balance = start
                else:
                    balance = int(row["balance"])
                con.commit()
                return balance

    async def balance(self, guild_id: int, user_id: int) -> int:
        return await self.ensure_account(guild_id, user_id)

    async def change(self, guild_id: int, user_id: int, delta: int,
                     kind: str, note: str, actor_id: Optional[int] = None) -> int:
        async with self.lock:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute(
                    "SELECT balance FROM accounts WHERE guild_id=? AND user_id=?",
                    (guild_id, user_id),
                ).fetchone()
                if row is None:
                    start = int(self.setting(guild_id, "starting_balance"))
                    con.execute(
                        "INSERT INTO accounts(guild_id,user_id,balance) VALUES(?,?,?)",
                        (guild_id, user_id, start),
                    )
                    current = start
                else:
                    current = int(row["balance"])
                # Do not let balances fall below zero.
                actual_delta = max(delta, -current)
                new_balance = current + actual_delta
                con.execute(
                    "UPDATE accounts SET balance=? WHERE guild_id=? AND user_id=?",
                    (new_balance, guild_id, user_id),
                )
                con.execute(
                    """INSERT INTO transactions
                       (guild_id,actor_id,target_id,amount,kind,note,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (guild_id, actor_id, user_id, actual_delta, kind, note, int(time.time())),
                )
                con.commit()
                return new_balance

    async def transfer(self, guild_id: int, sender_id: int, receiver_id: int,
                       amount: int) -> tuple[bool, int]:
        async with self.lock:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                for uid in (sender_id, receiver_id):
                    row = con.execute(
                        "SELECT 1 FROM accounts WHERE guild_id=? AND user_id=?",
                        (guild_id, uid),
                    ).fetchone()
                    if row is None:
                        start = int(self.setting(guild_id, "starting_balance"))
                        con.execute(
                            "INSERT INTO accounts(guild_id,user_id,balance) VALUES(?,?,?)",
                            (guild_id, uid, start),
                        )
                sender = con.execute(
                    "SELECT balance FROM accounts WHERE guild_id=? AND user_id=?",
                    (guild_id, sender_id),
                ).fetchone()
                if int(sender["balance"]) < amount:
                    con.rollback()
                    return False, int(sender["balance"])
                con.execute(
                    "UPDATE accounts SET balance=balance-? WHERE guild_id=? AND user_id=?",
                    (amount, guild_id, sender_id),
                )
                con.execute(
                    "UPDATE accounts SET balance=balance+? WHERE guild_id=? AND user_id=?",
                    (amount, guild_id, receiver_id),
                )
                now = int(time.time())
                con.executemany(
                    """INSERT INTO transactions
                       (guild_id,actor_id,target_id,amount,kind,note,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    [
                        (guild_id, sender_id, receiver_id, -amount, "transfer_out",
                         "Überweisung", now),
                        (guild_id, sender_id, receiver_id, amount, "transfer_in",
                         "Überweisung", now),
                    ],
                )
                con.commit()
                new_balance = int(sender["balance"]) - amount
                return True, new_balance

    async def cooldown_remaining(self, guild_id: int, user_id: int, command: str) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT expires_at FROM cooldowns WHERE guild_id=? AND user_id=? AND command=?",
                (guild_id, user_id, command),
            ).fetchone()
        return max(0, int(row["expires_at"]) - int(time.time())) if row else 0

    async def set_cooldown(self, guild_id: int, user_id: int, command: str, seconds: int):
        async with self.lock:
            with self._connect() as con:
                con.execute(
                    """INSERT INTO cooldowns(guild_id,user_id,command,expires_at)
                       VALUES(?,?,?,?)
                       ON CONFLICT(guild_id,user_id,command)
                       DO UPDATE SET expires_at=excluded.expires_at""",
                    (guild_id, user_id, command, int(time.time()) + seconds),
                )

    async def history(self, guild_id: int, user_id: int, limit: int = 8):
        with self._connect() as con:
            return con.execute(
                """SELECT amount,kind,note,created_at FROM transactions
                   WHERE guild_id=? AND (target_id=? OR actor_id=?)
                   ORDER BY id DESC LIMIT ?""",
                (guild_id, user_id, user_id, limit),
            ).fetchall()

    async def admin_adjust(self, guild_id: int, actor_id: int, user_id: int,
                           amount: int, note: str):
        async with self.lock:
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute(
                    "SELECT balance FROM accounts WHERE guild_id=? AND user_id=?",
                    (guild_id, user_id),
                ).fetchone()
                if row is None:
                    start = int(self.setting(guild_id, "starting_balance"))
                    con.execute(
                        "INSERT INTO accounts(guild_id,user_id,balance) VALUES(?,?,?)",
                        (guild_id, user_id, start),
                    )
                    current = start
                else:
                    current = int(row["balance"])
                delta = amount if amount >= 0 else max(amount, -current)
                new = current + delta
                con.execute(
                    "UPDATE accounts SET balance=? WHERE guild_id=? AND user_id=?",
                    (new, guild_id, user_id),
                )
                con.execute(
                    """INSERT INTO transactions
                       (guild_id,actor_id,target_id,amount,kind,note,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (guild_id, actor_id, user_id, delta, "admin", note, int(time.time())),
                )
                con.commit()
                return new


def fmt(amount: int, settings: dict[str, str]) -> str:
    return f"{amount:,}".replace(",", ".") + f" {settings['currency_emoji']}"


def duration(seconds: int) -> str:
    minutes = max(1, seconds // 60)
    if minutes >= 60:
        hours, mins = divmod(minutes, 60)
        return f"{hours} Std. {mins} Min." if mins else f"{hours} Std."
    return f"{minutes} Min."


class BankView(discord.ui.View):
    def __init__(self, store: EconomyStore, guild_id: int):
        super().__init__(timeout=180)
        self.store = store
        self.guild_id = guild_id

    @discord.ui.button(label="🏦 Meine Bank öffnen", style=discord.ButtonStyle.primary)
    async def open_bank(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            return await interaction.response.send_message("Diese Bank ist nur auf diesem Server verfügbar.", ephemeral=True)
        settings = self.store.settings(self.guild_id)
        balance = await self.store.balance(self.guild_id, interaction.user.id)
        embed = discord.Embed(
            title=f"🏦 {interaction.user.display_name}s Bank",
            description=f"**Kontostand:** {fmt(balance, settings)}",
            color=discord.Color.dark_gold(),
        )
        embed.set_footer(text="Nur du kannst diese Bankansicht sehen.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class EconomyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = EconomyStore()

    economy = app_commands.Group(name="wirtschaft", description="IronWolf-Wirtschaftssystem")

    @economy.command(name="setup", description="Wirtschaft für diesen Server konfigurieren")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        waehrung="Name der Serverwährung",
        emoji="Emoji/Symbol der Währung",
        startguthaben="Startguthaben für neue Konten",
        work_min="Mindestens so viel verdient man pro Arbeit",
        work_max="Höchstens so viel verdient man pro Arbeit",
        crime_min="Mindestens mögliche Beute bei Erfolg",
        crime_max="Höchstens mögliche Beute bei Erfolg",
        work_cooldown_min="Cooldown für .work in Minuten",
        crime_cooldown_min="Cooldown für .crime in Minuten",
        erfolg_prozent="Erfolgswahrscheinlichkeit für .crime (0–100)",
        polizei_prozent="Chance, von der Polizei erwischt zu werden (0–100)",
        strafe_prozent="Prozent des aktuellen Bargeldkontos, das als Strafe verloren geht",
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        waehrung: app_commands.Range[str, 1, 32] = "Hamburg-Dollar",
        emoji: app_commands.Range[str, 1, 12] = "💶",
        startguthaben: app_commands.Range[int, 0, 1_000_000_000] = 500,
        work_min: app_commands.Range[int, 1, 1_000_000] = 100,
        work_max: app_commands.Range[int, 1, 1_000_000] = 300,
        crime_min: app_commands.Range[int, 1, 1_000_000] = 150,
        crime_max: app_commands.Range[int, 1, 1_000_000] = 600,
        work_cooldown_min: app_commands.Range[int, 1, 10080] = 60,
        crime_cooldown_min: app_commands.Range[int, 1, 10080] = 30,
        erfolg_prozent: app_commands.Range[int, 0, 100] = 55,
        polizei_prozent: app_commands.Range[int, 0, 100] = 35,
        strafe_prozent: app_commands.Range[int, 0, 100] = 20,
    ):
        if not interaction.guild:
            return await interaction.response.send_message("Nur auf einem Server nutzbar.", ephemeral=True)
        if work_min > work_max or crime_min > crime_max:
            return await interaction.response.send_message("Die jeweiligen Mindestwerte dürfen nicht größer als die Höchstwerte sein.", ephemeral=True)
        values = {
            "currency_name": str(waehrung),
            "currency_emoji": str(emoji),
            "starting_balance": str(startguthaben),
            "work_min": str(work_min),
            "work_max": str(work_max),
            "crime_min": str(crime_min),
            "crime_max": str(crime_max),
            "work_cooldown": str(work_cooldown_min * 60),
            "crime_cooldown": str(crime_cooldown_min * 60),
            "crime_success_percent": str(erfolg_prozent),
            "police_catch_percent": str(polizei_prozent),
            "crime_fine_percent": str(strafe_prozent),
        }
        await self.store.set_settings(interaction.guild.id, values)
        await interaction.response.send_message(
            "✅ **Wirtschaft eingerichtet!**\n"
            f"Währung: {emoji} **{waehrung}**\n"
            f"Startguthaben: {fmt(startguthaben, values)}\n"
            f".work: {fmt(work_min, values)}–{fmt(work_max, values)} alle {duration(work_cooldown_min * 60)}\n"
            f".crime: {fmt(crime_min, values)}–{fmt(crime_max, values)} alle {duration(crime_cooldown_min * 60)}",
            ephemeral=True,
        )

    @commands.command(name="work")
    @commands.guild_only()
    async def work(self, ctx: commands.Context):
        guild_id, user_id = ctx.guild.id, ctx.author.id
        s = self.store.settings(guild_id)
        remaining = await self.store.cooldown_remaining(guild_id, user_id, "work")
        if remaining:
            return await ctx.reply(f"⏳ Du kannst erst in **{duration(remaining)}** wieder arbeiten.", mention_author=False)
        await self.store.set_cooldown(guild_id, user_id, "work", int(s["work_cooldown"]))
        amount = random.randint(int(s["work_min"]), int(s["work_max"]))
        jobs = [
            "hast als Kurier gearbeitet",
            "hast im Café ausgeholfen",
            "hast eine Lieferung erledigt",
            "hast bei einer Werkstatt geholfen",
            "hast einen Auftrag im Hafen erledigt",
        ]
        await self.store.change(guild_id, user_id, amount, "work", " ".join(random.choice(jobs).split()).capitalize(), user_id)
        await ctx.reply(f"🧰 {ctx.author.mention}, du {random.choice(jobs)} und verdienst **{fmt(amount, s)}**!", mention_author=False)

    @commands.command(name="crime")
    @commands.guild_only()
    async def crime(self, ctx: commands.Context):
        guild_id, user_id = ctx.guild.id, ctx.author.id
        s = self.store.settings(guild_id)
        remaining = await self.store.cooldown_remaining(guild_id, user_id, "crime")
        if remaining:
            return await ctx.reply(f"⏳ Du kannst erst in **{duration(remaining)}** wieder einen Crime-Versuch starten.", mention_author=False)
        await self.store.set_cooldown(guild_id, user_id, "crime", int(s["crime_cooldown"]))
        current = await self.store.balance(guild_id, user_id)
        if random.randint(1, 100) <= int(s["crime_success_percent"]):
            amount = random.randint(int(s["crime_min"]), int(s["crime_max"]))
            await self.store.change(guild_id, user_id, amount, "crime_win", "Crime-Erfolg", user_id)
            return await ctx.reply(f"🕶️ **Erfolg!** Du hast Beute gemacht und erhältst **{fmt(amount, s)}**.", mention_author=False)
        if random.randint(1, 100) <= int(s["police_catch_percent"]):
            fine = max(1, current * int(s["crime_fine_percent"]) // 100) if current else 0
            if fine:
                await self.store.change(guild_id, user_id, -fine, "crime_fine", "Fiktive RP-Polizeistrafe", user_id)
            return await ctx.reply(
                f"🚔 **Die Polizei hat dich erwischt!** Fiktive RP-Strafe: **{fmt(fine, s)}**.\n"
                "Dies ist nur eine Spielmechanik für euer RP.",
                mention_author=False,
            )
        loss = min(current, random.randint(int(s["crime_min"]), int(s["crime_max"])))
        if loss:
            await self.store.change(guild_id, user_id, -loss, "crime_loss", "Crime fehlgeschlagen", user_id)
        return await ctx.reply(f"💨 **Fehlgeschlagen!** Du bist entkommen, hast aber **{fmt(loss, s)}** verloren.", mention_author=False)

    @commands.command(name="bank")
    @commands.guild_only()
    async def bank(self, ctx: commands.Context):
        # Public launcher; the actual account view is private.
        await ctx.reply(
            "Drücke den Button, um deine Bank **privat** zu öffnen.",
            view=BankView(self.store, ctx.guild.id),
            mention_author=False,
        )

    @commands.command(name="pay")
    @commands.guild_only()
    async def pay(self, ctx: commands.Context, member: discord.Member, amount: int):
        if member.bot or member.id == ctx.author.id:
            return await ctx.reply("Du kannst nur an andere Spieler überweisen.", mention_author=False)
        if amount <= 0:
            return await ctx.reply("Der Betrag muss größer als 0 sein.", mention_author=False)
        ok, balance = await self.store.transfer(ctx.guild.id, ctx.author.id, member.id, amount)
        s = self.store.settings(ctx.guild.id)
        if not ok:
            return await ctx.reply(f"❌ Nicht genug Guthaben. Dein Kontostand: **{fmt(balance, s)}**.", mention_author=False)
        await ctx.reply(f"✅ {ctx.author.mention} hat **{fmt(amount, s)}** an {member.mention} überwiesen.", mention_author=False)

    @commands.command(name="transactions")
    @commands.guild_only()
    async def transactions(self, ctx: commands.Context):
        rows = await self.store.history(ctx.guild.id, ctx.author.id)
        s = self.store.settings(ctx.guild.id)
        if not rows:
            return await ctx.reply("Noch keine Transaktionen vorhanden.", mention_author=False)
        lines = []
        for row in rows:
            stamp = f"<t:{int(row['created_at'])}:R>"
            amount = int(row["amount"])
            sign = "+" if amount > 0 else ""
            lines.append(f"• {stamp} — **{sign}{fmt(amount, s)}** · {row['note']}")
        embed = discord.Embed(title="📜 Deine letzten Transaktionen", description="\n".join(lines), color=discord.Color.gold())
        await ctx.reply(embed=embed, mention_author=False)

    @commands.command(name="cashadd")
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def cashadd(self, ctx: commands.Context, member: discord.Member, amount: int, *, grund: str = "Admin-Anpassung"):
        if amount == 0:
            return await ctx.reply("Der Betrag darf nicht 0 sein.", mention_author=False)
        balance = await self.store.admin_adjust(ctx.guild.id, ctx.author.id, member.id, amount, grund[:200])
        s = self.store.settings(ctx.guild.id)
        await ctx.reply(f"✅ Kontostand von {member.mention}: **{fmt(balance, s)}**.", mention_author=False)

    @commands.command(name="economyhelp")
    @commands.guild_only()
    async def economyhelp(self, ctx: commands.Context):
        embed = discord.Embed(
            title="🐺 IronWolf Wirtschaft",
            description=(
                "`.bank` – private Bank öffnen\n"
                "`.work` – arbeiten und Geld verdienen\n"
                "`.crime` – riskanter Crime-Versuch\n"
                "`.pay @Spieler Betrag` – Geld überweisen\n"
                "`.transactions` – eigene letzten Transaktionen\n"
                "`/wirtschaft setup` – Wirtschaft konfigurieren (Admins)\n"
                "`.cashadd @Spieler Betrag [Grund]` – Guthaben anpassen (Admins; negative Beträge ziehen Geld ab)"
            ),
            color=discord.Color.dark_gold(),
        )
        await ctx.reply(embed=embed, mention_author=False)

    @commands.Cog.listener()
    async def on_ready(self):
        # Don't sync here: it may run on every reconnect and cause rate limits.
        pass

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            message = "❌ Dafür brauchst du Administrator-Rechte."
        else:
            message = "❌ Beim Wirtschaftssystem ist ein Fehler aufgetreten."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def setup_economy(bot: commands.Bot, *, sync_slash_commands: bool = False):
    """
    Add the economy cog to a bot.

    If your bot already syncs app commands, leave sync_slash_commands=False.
    If it does not, you can call:
        setup_economy(bot, sync_slash_commands=True)
    This schedules slash-command sync after the bot is ready.
    """
    async def _load():
        await bot.add_cog(EconomyCog(bot))
        if sync_slash_commands:
            await bot.wait_until_ready()
            try:
                await bot.tree.sync()
            except Exception as exc:
                print(f"[IronWolf Economy] Slash-command sync failed: {exc}")

    # bot.add_cog is async in discord.py 2.x
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        raise RuntimeError(
            "setup_economy(bot) muss innerhalb eines laufenden Bot-Events aufgerufen werden. "
            "Nutze in deinem async setup_hook: await bot.add_cog(EconomyCog(bot)) "
            "oder verwende die Einbindungshinweise in der README."
        )
    loop.create_task(_load())


async def setup(bot: commands.Bot):
    """Extension entrypoint, usable with await bot.load_extension('ironwolf_economy')."""
    await bot.add_cog(EconomyCog(bot))

