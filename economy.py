import json, random, time
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands

FILE = Path("economy.json")
try:
    DB = json.loads(FILE.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    DB = {"guilds": {}}
DB.setdefault("guilds", {})

def save():
    FILE.write_text(json.dumps(DB, ensure_ascii=False, indent=2), encoding="utf-8")

def guild(gid):
    return DB["guilds"].setdefault(str(gid), {"users": {}, "shop": {}, "bank_limit": 100000, "bank_interest": 0.0, "tax_enabled": False, "tax_percent": 5.0})

def user(gid, uid):
    return guild(gid)["users"].setdefault(str(uid), {"bank": 500, "cash": 0, "work_at": 0, "crime_at": 0, "items": []})

def money(n):
    return f"{int(n):,}".replace(",", ".") + " €"

def credit(g, u, gross):
    tax = int(gross * float(g.get("tax_percent", 5)) / 100) if g.get("tax_enabled", False) else 0
    net = gross - tax
    room = max(0, int(g.get("bank_limit", 100000)) - int(u["bank"]))
    actual = min(room, net)
    u["bank"] += actual
    return actual, tax, net - actual

def admin():
    return app_commands.checks.has_permissions(manage_guild=True)

class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="work")
    @commands.guild_only()
    async def work(self, ctx):
        g, u = guild(ctx.guild.id), user(ctx.guild.id, ctx.author.id)
        wait = 60 - (time.time() - u["work_at"])
        if wait > 0:
            await ctx.reply(f"Du kannst in {int(wait)} Sekunden wieder arbeiten.", mention_author=False); return
        gross = random.randint(120, 350)
        actual, tax, capped = credit(g, u, gross)
        u["work_at"] = time.time(); save()
        extra = f" Steuer: {money(tax)}." if tax else ""
        extra += f" Wegen des Banklimits nicht gutgeschrieben: {money(capped)}." if capped else ""
        await ctx.reply(f"🛠️ Du hast {money(actual)} verdient und direkt auf dein Bankkonto erhalten.{extra}\nKontostand: **{money(u['bank'])}**", mention_author=False)

    @commands.command(name="crime")
    @commands.guild_only()
    async def crime(self, ctx):
        g, u = guild(ctx.guild.id), user(ctx.guild.id, ctx.author.id)
        wait = 300 - (time.time() - u["crime_at"])
        if wait > 0:
            await ctx.reply(f"🚨 Warte noch {int(wait)} Sekunden.", mention_author=False); return
        u["crime_at"] = time.time()
        if random.random() < 0.60:
            gross = random.randint(200, 650)
            actual, tax, capped = credit(g, u, gross)
            result = f"💸 Coup erfolgreich! {money(actual)} wurden deinem Bankkonto gutgeschrieben."
            if tax: result += f" Steuer: {money(tax)}."
            if capped: result += f" Wegen des Banklimits nicht gutgeschrieben: {money(capped)}."
        else:
            loss = random.randint(100, 300)
            lost = min(u["bank"], loss); u["bank"] -= lost
            result = f"🚔 Du wurdest erwischt und hast {money(lost)} verloren."
        save()
        await ctx.reply(f"{result}\n🏦 Kontostand: **{money(u['bank'])}**", mention_author=False)

    @commands.command(name="pay")
    @commands.guild_only()
    async def pay(self, ctx, member: discord.Member = None, amount: int = None):
        if member is None or amount is None:
            await ctx.reply("Verwendung: !pay @Mitglied Betrag", mention_author=False); return
        if member.bot or member.id == ctx.author.id or amount <= 0:
            await ctx.reply("Wähle ein anderes Mitglied und einen Betrag größer als 0.", mention_author=False); return
        sender, receiver = user(ctx.guild.id, ctx.author.id), user(ctx.guild.id, member.id)
        if sender["bank"] < amount:
            await ctx.reply("Du hast nicht genug Geld auf deinem Bankkonto.", mention_author=False); return
        sender["bank"] -= amount; receiver["bank"] += amount; save()
        await ctx.reply(f"💸 {ctx.author.mention} hat {member.mention} **{money(amount)}** überwiesen.", mention_author=False)

    @app_commands.command(name="bank", description="Öffentliche Bankübersicht und dein Kontostand")
    async def bank_overview(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True); return
        g, u = guild(interaction.guild_id), user(interaction.guild_id, interaction.user.id)
        e = discord.Embed(title="🏦 IronWolf Bank", description="Öffentliche Bankübersicht. Nutze /konto für deinen privaten Kontostand.", color=discord.Color.blurple())
        e.add_field(name="Zinsen", value=f"{g['bank_interest']:.2f} %", inline=True)
        e.add_field(name="Steuern", value=f"{'AN' if g['tax_enabled'] else 'AUS'} · {g['tax_percent']:.1f} %", inline=True)
        e.add_field(name="Banklimit", value=money(g["bank_limit"]), inline=True)
        await interaction.response.send_message(embed=e, ephemeral=False)

    @app_commands.command(name="konto", description="Zeigt deinen Kontostand privat")
    async def account(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True); return
        u = user(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(f"🏦 Bankkonto: **{money(u['bank'])}**\n💶 Bargeld: **{money(u['cash'])}**", ephemeral=True)

    @app_commands.command(name="bank-zinsen", description="Bankzinssatz einstellen (Admin)")
    @admin()
    async def bank_interest(self, interaction: discord.Interaction, prozent: app_commands.Range[float, 0, 10]):
        guild(interaction.guild_id)["bank_interest"] = float(prozent); save()
        await interaction.response.send_message(f"Zinssatz auf {prozent:.2f} % gesetzt.", ephemeral=True)

    @app_commands.command(name="bank-limit", description="Maximales Bankguthaben einstellen (Admin)")
    @admin()
    async def bank_limit(self, interaction: discord.Interaction, betrag: app_commands.Range[int, 0, 1000000000]):
        guild(interaction.guild_id)["bank_limit"] = betrag; save()
        await interaction.response.send_message(f"Banklimit auf {money(betrag)} gesetzt.", ephemeral=True)

    @app_commands.command(name="steuern", description="Steuern an- oder ausschalten (Admin)")
    @admin()
    @app_commands.choices(status=[app_commands.Choice(name="An", value="an"), app_commands.Choice(name="Aus", value="aus")])
    async def taxes(self, interaction: discord.Interaction, status: app_commands.Choice[str], prozent: app_commands.Range[float, 0, 50] = 5.0):
        g = guild(interaction.guild_id); g["tax_enabled"] = status.value == "an"; g["tax_percent"] = float(prozent); save()
        await interaction.response.send_message(f"Steuern sind **{'AN' if g['tax_enabled'] else 'AUS'}**. Satz für neue Einnahmen: {prozent:.1f} %.", ephemeral=True)

    @app_commands.command(name="shop", description="Öffentliche Shopübersicht mit Artikeln und Preisen")
    async def shop_overview(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True); return
        items = guild(interaction.guild_id)["shop"]
        e = discord.Embed(title="🛒 IronWolf-Shop", description="Artikel und Preise für alle Mitglieder", color=discord.Color.gold())
        if not items:
            e.description = "Der Shop ist noch leer. Admins können /shop-hinzufuegen verwenden."
        else:
            for name, item in list(items.items())[:25]:
                e.add_field(name=name[:256], value=f"Preis: **{money(item['price'])}**\n{item.get('description') or 'Keine Beschreibung'}", inline=True)
        e.set_footer(text="Kaufen: /shop-kaufen")
        await interaction.response.send_message(embed=e, ephemeral=False)

    @app_commands.command(name="shop-hinzufuegen", description="Shopartikel hinzufügen (Admin)")
    @admin()
    async def shop_add(self, interaction: discord.Interaction, name: str, preis: app_commands.Range[int, 1, 100000000], beschreibung: str = ""):
        name = name.strip()[:50]
        if not name:
            await interaction.response.send_message("Bitte Artikelnamen eingeben.", ephemeral=True); return
        guild(interaction.guild_id)["shop"][name.lower()] = {"price": preis, "description": beschreibung[:150]}; save()
        await interaction.response.send_message(f"Artikel **{name}** für **{money(preis)}** hinzugefügt.", ephemeral=True)

    @app_commands.command(name="shop-entfernen", description="Shopartikel entfernen (Admin)")
    @admin()
    async def shop_remove(self, interaction: discord.Interaction, name: str):
        item = guild(interaction.guild_id)["shop"].pop(name.lower(), None); save()
        await interaction.response.send_message("Artikel entfernt." if item else "Artikel nicht gefunden.", ephemeral=True)

    @app_commands.command(name="shop-kaufen", description="Shopartikel kaufen")
    async def shop_buy(self, interaction: discord.Interaction, name: str):
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True); return
        g, u = guild(interaction.guild_id), user(interaction.guild_id, interaction.user.id)
        item = g["shop"].get(name.lower())
        if not item:
            await interaction.response.send_message("Artikel nicht gefunden. Nutze /shop.", ephemeral=True); return
        if u["bank"] < item["price"]:
            await interaction.response.send_message("Du hast nicht genug Geld auf dem Bankkonto.", ephemeral=True); return
        u["bank"] -= item["price"]; u["items"].append(name.lower()); save()
        await interaction.response.send_message(f"🛍️ **{name}** für **{money(item['price'])}** gekauft.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Economy(bot))
