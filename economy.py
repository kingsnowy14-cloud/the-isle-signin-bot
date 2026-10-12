import json
import random
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

STORE = Path("economy.json")
_state = {}
try:
    _state = json.loads(STORE.read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    _state = {"guilds": {}}
_state.setdefault("guilds", {})


def _save():
    STORE.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8")


def _guild(gid):
    return _state["guilds"].setdefault(str(gid), {"users": {}, "shop": {}, "bank_interest": 1.0, "bank_limit": 100000})


def _user(gid, uid):
    return _guild(gid)["users"].setdefault(str(uid), {"cash": 500, "bank": 0, "work_at": 0, "crime_at": 0, "items": []})


def _money(n):
    return f"{n:,}".replace(",", ".") + " €"


def _admin():
    return app_commands.checks.has_permissions(manage_guild=True)


class Economy(commands.Cog):
    """IronWolf economy system."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="work")
    @commands.guild_only()
    async def work(self, ctx):
        u = _user(ctx.guild.id, ctx.author.id)
        now = time.time()
        wait = 60 - (now - u["work_at"])
        if wait > 0:
            await ctx.reply(f"Du kannst erst in {int(wait)} Sekunden wieder arbeiten.", mention_author=False)
            return
        earned = random.randint(120, 350)
        u["cash"] += earned
        u["work_at"] = now
        _save()
        await ctx.reply(f"🛠️ Schicht abgeschlossen: +{_money(earned)}. Bargeld: {_money(u['cash'])}", mention_author=False)

    @commands.command(name="crime")
    @commands.guild_only()
    async def crime(self, ctx):
        u = _user(ctx.guild.id, ctx.author.id)
        now = time.time()
        wait = 300 - (now - u["crime_at"])
        if wait > 0:
            await ctx.reply(f"🚨 Warte noch {int(wait)} Sekunden bis zum nächsten Versuch.", mention_author=False)
            return
        u["crime_at"] = now
        if random.random() < 0.65:
            amount = random.randint(200, 650)
            u["cash"] += amount
            msg = f"💸 Coup erfolgreich: +{_money(amount)}."
        else:
            amount = min(u["cash"], random.randint(100, 300))
            u["cash"] -= amount
            msg = f"🚔 Erwischt! Strafe: {_money(amount)}."
        _save()
        await ctx.reply(f"{msg} Bargeld: {_money(u['cash'])}", mention_author=False)

    @commands.command(name="pay")
    @commands.guild_only()
    async def pay(self, ctx, member: discord.Member = None, amount: int = None):
        if member is None or amount is None:
            await ctx.reply("Verwendung: !pay @Mitglied Betrag", mention_author=False)
            return
        if member.bot or member.id == ctx.author.id or amount <= 0:
            await ctx.reply("Bitte wähle ein anderes Mitglied und einen Betrag größer als 0.", mention_author=False)
            return
        sender = _user(ctx.guild.id, ctx.author.id)
        receiver = _user(ctx.guild.id, member.id)
        if sender["cash"] < amount:
            await ctx.reply("Du hast nicht genug Bargeld.", mention_author=False)
            return
        sender["cash"] -= amount
        receiver["cash"] += amount
        _save()
        await ctx.reply(f"💸 {ctx.author.mention} hat {member.mention} {_money(amount)} überwiesen.", mention_author=False)

    @app_commands.command(name="konto", description="Zeigt Bargeld und Bankguthaben")
    async def konto(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
            return
        u = _user(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(title="🏦 IronWolf-Konto", color=discord.Color.green())
        embed.add_field(name="Bargeld", value=_money(u["cash"]))
        embed.add_field(name="Bank", value=_money(u["bank"]))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="einzahlen", description="Zahlt Bargeld auf dein Bankkonto ein")
    async def einzahlen(self, interaction: discord.Interaction, betrag: app_commands.Range[int, 1, 100000000]):
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
            return
        u = _user(interaction.guild_id, interaction.user.id)
        if betrag > u["cash"]:
            await interaction.response.send_message("Du hast nicht genug Bargeld.", ephemeral=True)
            return
        u["cash"] -= betrag
        u["bank"] += betrag
        _save()
        await interaction.response.send_message(f"Eingezahlt: {_money(betrag)}. Bank: {_money(u['bank'])}", ephemeral=True)

    @app_commands.command(name="abheben", description="Hebt Geld vom Bankkonto ab")
    async def abheben(self, interaction: discord.Interaction, betrag: app_commands.Range[int, 1, 100000000]):
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
            return
        u = _user(interaction.guild_id, interaction.user.id)
        if betrag > u["bank"]:
            await interaction.response.send_message("Nicht genug Guthaben auf der Bank.", ephemeral=True)
            return
        u["bank"] -= betrag
        u["cash"] += betrag
        _save()
        await interaction.response.send_message(f"Abgehoben: {_money(betrag)}. Bargeld: {_money(u['cash'])}", ephemeral=True)

    bank = app_commands.Group(name="bank", description="Bank konfigurieren")

    @bank.command(name="einstellungen", description="Zeigt die Bankeinstellungen (Admins)")
    @_admin()
    async def bank_settings(self, interaction: discord.Interaction):
        g = _guild(interaction.guild_id)
        embed = discord.Embed(title="Bank-Konfiguration", color=discord.Color.blurple())
        embed.add_field(name="Zinssatz", value=f"{g['bank_interest']:.2f} %")
        embed.add_field(name="Maximales Guthaben", value=_money(g["bank_limit"]))
        embed.description = "Nutze /bank zinsen oder /bank limit zum Ändern."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bank.command(name="zinsen", description="Setzt den Zinssatz in Prozent (Admins)")
    @_admin()
    async def bank_interest(self, interaction: discord.Interaction, prozent: app_commands.Range[float, 0, 10]):
        _guild(interaction.guild_id)["bank_interest"] = float(prozent)
        _save()
        await interaction.response.send_message(f"Bank-Zinssatz auf {prozent:.2f} % gesetzt.", ephemeral=True)

    @bank.command(name="limit", description="Setzt das maximale Bankguthaben (Admins)")
    @_admin()
    async def bank_limit(self, interaction: discord.Interaction, betrag: app_commands.Range[int, 0, 1000000000]):
        _guild(interaction.guild_id)["bank_limit"] = betrag
        _save()
        await interaction.response.send_message(f"Banklimit auf {_money(betrag)} gesetzt.", ephemeral=True)

    shop = app_commands.Group(name="shop", description="Shop verwalten und ansehen")

    @shop.command(name="liste", description="Zeigt Shop-Artikel")
    async def shop_list(self, interaction: discord.Interaction):
        items = _guild(interaction.guild_id)["shop"]
        embed = discord.Embed(title="IronWolf-Shop", color=discord.Color.gold())
        embed.description = "\n".join(f"**{name}** — {_money(item['price'])}\n{item.get('description', '')}" for name, item in items.items()) or "Shop leer. Admins können Artikel mit /shop hinzufuegen anlegen."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @shop.command(name="hinzufuegen", description="Fügt einen Artikel hinzu (Admins)")
    @_admin()
    async def shop_add(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 50], preis: app_commands.Range[int, 1, 100000000], beschreibung: str = ""):
        _guild(interaction.guild_id)["shop"][name.lower()] = {"price": preis, "description": beschreibung[:150]}
        _save()
        await interaction.response.send_message(f"Artikel {name} für {_money(preis)} hinzugefügt.", ephemeral=True)

    @shop.command(name="entfernen", description="Entfernt einen Artikel (Admins)")
    @_admin()
    async def shop_remove(self, interaction: discord.Interaction, name: str):
        removed = _guild(interaction.guild_id)["shop"].pop(name.lower(), None)
        _save()
        await interaction.response.send_message("Artikel entfernt." if removed else "Artikel nicht gefunden.", ephemeral=True)

    @shop.command(name="kaufen", description="Kauft einen Shop-Artikel")
    async def shop_buy(self, interaction: discord.Interaction, name: str):
        g = _guild(interaction.guild_id)
        item = g["shop"].get(name.lower())
        if not item:
            await interaction.response.send_message("Artikel nicht gefunden. Nutze /shop liste.", ephemeral=True)
            return
        u = _user(interaction.guild_id, interaction.user.id)
        if u["cash"] < item["price"]:
            await interaction.response.send_message("Du hast nicht genug Bargeld.", ephemeral=True)
            return
        u["cash"] -= item["price"]
        u["items"].append(name.lower())
        _save()
        await interaction.response.send_message(f"Gekauft: {name} für {_money(item['price'])}.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Economy(bot))
