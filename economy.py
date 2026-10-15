import json, random, time
from pathlib import Path
from storage import data_path
import discord
from discord import app_commands
from discord.ext import commands

FILE = data_path("economy.json")
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


class BankView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Mein Konto ansehen", style=discord.ButtonStyle.primary, emoji="🏦", custom_id="ironwolf:bank:account")
    async def show_account(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
            return
        u = user(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            f"🏦 Bankkonto: **{money(u['bank'])}**\n💶 Bargeld: **{money(u['cash'])}**",
            ephemeral=True
        )


class ShopPurchaseView(discord.ui.View):
    def __init__(self, guild_id: int, items: dict):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.items = dict(items)
        self.selected_name = None
        options = [
            discord.SelectOption(label=name[:100], description=f"{money(item['price'])} · {(item.get('description') or 'Keine Beschreibung')[:70]}", value=name)
            for name, item in list(items.items())[:25]
        ]
        self.add_item(ShopItemSelect(options, self))
        self.add_item(ShopBuyButton(self))


class ShopItemSelect(discord.ui.Select):
    def __init__(self, options, parent_view):
        super().__init__(placeholder="Artikel zum Kaufen auswählen …", min_values=1, max_values=1, options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_name = self.values[0]
        item = self.parent_view.items.get(self.values[0])
        if item:
            await interaction.response.send_message(
                f"Ausgewählt: **{self.values[0]}** für **{money(item['price'])}**. Drücke anschließend auf „Jetzt kaufen“.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("Dieser Artikel ist nicht mehr verfügbar.", ephemeral=True)


class ShopBuyButton(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(label="Jetzt kaufen", style=discord.ButtonStyle.success, emoji="🛍️")
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild_id != self.parent_view.guild_id:
            await interaction.response.send_message("Dieser Shop gehört zu einem anderen Server.", ephemeral=True)
            return
        name = self.parent_view.selected_name
        if not name:
            await interaction.response.send_message("Wähle zuerst oben einen Artikel aus.", ephemeral=True)
            return
        g = guild(interaction.guild_id)
        item = self.parent_view.items.get(name)
        if not item:
            await interaction.response.send_message("Artikel nicht gefunden. Öffne /shop erneut.", ephemeral=True)
            return
        u = user(interaction.guild_id, interaction.user.id)
        price = int(item["price"])
        if u["bank"] < price:
            await interaction.response.send_message(f"Nicht genug Geld auf deinem Bankkonto. Preis: **{money(price)}**.", ephemeral=True)
            return
        u["bank"] -= price
        u["items"].append(name)
        save()
        await interaction.response.send_message(
            f"🛍️ Du hast **{name}** für **{money(price)}** gekauft. Neuer Kontostand: **{money(u['bank'])}**.",
            ephemeral=True
        )


class CandyShopPurchaseView(discord.ui.View):
    """Öffentlicher Dashboard-Candyshop; bezahlt wird mit Halloween-Süßigkeiten."""
    def __init__(self, guild_id: int, items: dict):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.items = dict(items)
        self.selected_name = None
        options = [
            discord.SelectOption(
                label=name[:100],
                description=f"{sweets_amount(item['price'])} · {(item.get('description') or 'Keine Beschreibung')[:70]}",
                value=name
            )
            for name, item in list(items.items())[:25]
        ]
        self.add_item(CandyShopSelect(options, self))
        self.add_item(CandyShopBuyButton(self))


class CandyShopSelect(discord.ui.Select):
    def __init__(self, options, parent_view):
        super().__init__(placeholder="Candyshop-Artikel auswählen …", min_values=1, max_values=1, options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_name = self.values[0]
        item = self.parent_view.items.get(self.values[0])
        if item:
            await interaction.response.send_message(
                f"Ausgewählt: **{self.values[0]}** für **{sweets_amount(item['price'])}**. Klicke auf „Mit 🍬 kaufen“.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("Dieser Artikel ist nicht mehr verfügbar.", ephemeral=True)


class CandyShopBuyButton(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(label="Mit 🍬 kaufen", style=discord.ButtonStyle.success, emoji="🛍️")
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild_id != self.parent_view.guild_id:
            await interaction.response.send_message("Dieser Shop gehört zu einem anderen Server.", ephemeral=True)
            return
        name = self.parent_view.selected_name
        item = self.parent_view.items.get(name) if name else None
        if not item:
            await interaction.response.send_message("Wähle zuerst einen Artikel aus.", ephemeral=True)
            return
        u = halloween_user(interaction.guild_id, interaction.user.id)
        price = int(item["price"])
        if int(u.get("sweets", 0)) < price:
            await interaction.response.send_message(
                f"Du hast nicht genug 🍬. Preis: **{sweets_amount(price)}**, dein Vorrat: **{sweets_amount(u.get('sweets', 0))}**.",
                ephemeral=True
            )
            return
        u["sweets"] -= price
        u.setdefault("halloween_items", []).append(name)
        u["candyshop_purchases"] = int(u.get("candyshop_purchases", 0)) + 1
        save()
        await interaction.response.send_message(
            f"🎉 Gekauft: **{name}** für **{sweets_amount(price)}**! Du hast noch **{sweets_amount(u['sweets'])}**.",
            ephemeral=True
        )


class ShopView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Artikel kaufen", style=discord.ButtonStyle.success, emoji="🛒", custom_id="ironwolf:shop:buy")
    async def open_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id is None:
            await interaction.response.send_message("Nur auf einem Server verfügbar.", ephemeral=True)
            return
        items = dict(guild(interaction.guild_id)["shop"])
        if not items:
            await interaction.response.send_message("Der Shop ist noch leer.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Wähle einen Artikel aus und drücke danach auf **Jetzt kaufen**:",
            view=ShopPurchaseView(interaction.guild_id, items),
            ephemeral=True
        )


def admin():
    return app_commands.checks.has_permissions(manage_guild=True)

class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="candyshop", description="Sendet den öffentlichen Candyshop mit kaufbaren Artikeln in diesen Kanal")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def candyshop(self, interaction: discord.Interaction):
        config_path = data_path("config.json")
        try:
            config_data = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config_data = {}
        server_cfg = config_data.get(str(interaction.guild_id), {})
        seasonal = server_cfg.get("seasonal_events", {})
        shop_cfg = seasonal.get("candyshop", {})
        items = shop_cfg.get("items", [])
        if not items:
            await interaction.response.send_message("🍬 Der Candyshop ist noch leer. Lege zuerst im Dashboard unter Saison-Events Artikel und Preise an.", ephemeral=True)
            return
        shop_items = {}
        for item in items[:25]:
            name = str(item.get("name", "")).strip()[:100]
            try:
                price = max(1, int(item.get("price", 0)))
            except (TypeError, ValueError):
                continue
            if name:
                shop_items[name] = {"price": price, "description": str(item.get("description", ""))[:100]}
        if not shop_items:
            await interaction.response.send_message("Im Dashboard sind keine gültigen Artikel hinterlegt.", ephemeral=True)
            return
        embed = discord.Embed(title="🍬 IronWolf · Candyshop", description="Wähle einen Artikel aus und kaufe ihn mit deinen gesammelten Halloween-Süßigkeiten.", color=discord.Color.from_rgb(255, 105, 180))
        for name, item in shop_items.items():
            desc = item.get("description") or "Ein exklusiver Shop-Artikel."
            embed.add_field(name=f"{name} · {sweets_amount(item['price'])}", value=desc, inline=False)
        embed.set_footer(text="Artikel und Preise werden vom Server-Team im Dashboard verwaltet.")
        await interaction.response.send_message(embed=embed, view=CandyShopPurchaseView(interaction.guild_id, shop_items), ephemeral=False)

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
        await interaction.response.send_message(embed=e, view=BankView(), ephemeral=False)

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
        await interaction.response.send_message(embed=e, view=ShopView(), ephemeral=False)

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


# Halloween-Spezialwirtschaft: Süßigkeiten als eigene, dauerhaft gespeicherte Währung.
HALLOWEEN_SHOP = {
    "geistermaske": {"price": 35, "description": "Eine gruselige Geistermaske 👻"},
    "kuerbis": {"price": 60, "description": "Ein leuchtender Halloween-Kürbis 🎃"},
    "fledermaus": {"price": 100, "description": "Ein kleiner Fledermaus-Begleiter 🦇"},
    "spukumhang": {"price": 180, "description": "Ein geheimnisvoller Spukumhang 🧛"},
    "verfluchter-kristall": {"price": 300, "description": "Ein seltener verfluchter Kristall 🔮"},
}

def halloween_user(gid, uid):
    record = user(gid, uid)
    record.setdefault("sweets", 0)
    record.setdefault("sweets_at", 0)
    record.setdefault("spook_at", 0)
    record.setdefault("halloween_items", [])
    return record

def sweets_amount(value):
    return f"{int(value):,}".replace(",", ".") + " 🍬"

class HalloweenEconomy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="süßes", aliases=["suesses", "susses"])
    @commands.guild_only()
    async def farm_sweets(self, ctx):
        u = halloween_user(ctx.guild.id, ctx.author.id)
        wait = 86400 - (time.time() - float(u.get("sweets_at", 0)))
        if wait > 0:
            await ctx.reply(f"🎃 Du kannst in {int(wait)} Sekunden wieder Süßigkeiten sammeln.", mention_author=False)
            return
        amount = random.randint(8, 24)
        u["sweets"] += amount
        u["sweets_earned_total"] = int(u.get("sweets_earned_total", 0)) + amount
        u["sweets_at"] = time.time()
        save()
        await ctx.reply(f"🍬 Du hast {sweets_amount(amount)} gesammelt! Vorrat: **{sweets_amount(u['sweets'])}**.", mention_author=False)

    @app_commands.command(name="süßes", description="Sammle Halloween-Süßigkeiten")
    @app_commands.guild_only()
    async def farm_sweets_slash(self, interaction: discord.Interaction):
        u = halloween_user(interaction.guild_id, interaction.user.id)
        wait = 60 - (time.time() - float(u.get("sweets_at", 0)))
        if wait > 0:
            await interaction.response.send_message(f"🎃 Du kannst in {int(wait)} Sekunden wieder sammeln.", ephemeral=True)
            return
        amount = random.randint(8, 24)
        u["sweets"] += amount
        u["sweets_at"] = time.time()
        save()
        await interaction.response.send_message(f"🍬 Du hast {sweets_amount(amount)} gesammelt! Vorrat: **{sweets_amount(u['sweets'])}**.")

    @commands.command(name="spook")
    @commands.guild_only()
    async def spook(self, ctx, member: discord.Member = None):
        if member is None:
            await ctx.reply("Verwendung: !spook @Mitglied — versuche, Süßigkeiten zu stibitzen!", mention_author=False)
            return
        if member.bot or member.id == ctx.author.id:
            await ctx.reply("👻 Du kannst nur bei einem anderen echten Mitglied spooken.", mention_author=False)
            return
        thief = halloween_user(ctx.guild.id, ctx.author.id)
        victim = halloween_user(ctx.guild.id, member.id)
        wait = 300 - (time.time() - float(thief.get("spook_at", 0)))
        if wait > 0:
            await ctx.reply(f"🦇 Spook ist in {int(wait)} Sekunden wieder verfügbar.", mention_author=False)
            return
        thief["spook_at"] = time.time()
        if random.random() < 0.65 and victim["sweets"] > 0:
            stolen = min(victim["sweets"], random.randint(5, 20))
            victim["sweets"] -= stolen
            thief["sweets"] += stolen
            result = f"👻 {ctx.author.mention} hat {sweets_amount(stolen)} von {member.mention} geklaut!"
        else:
            fine = min(thief["sweets"], random.randint(2, 8))
            thief["sweets"] -= fine
            result = f"🎃 Erwischt! Die Geister haben {sweets_amount(fine)} einkassiert."
        save()
        await ctx.reply(result, mention_author=False, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="spook", description="Versuche, einem Mitglied Halloween-Süßigkeiten zu klauen")
    @app_commands.guild_only()
    async def spook_slash(self, interaction: discord.Interaction, mitglied: discord.Member):
        if mitglied.bot or mitglied.id == interaction.user.id:
            await interaction.response.send_message("👻 Du kannst nur bei einem anderen echten Mitglied spooken.", ephemeral=True)
            return
        thief = halloween_user(interaction.guild_id, interaction.user.id)
        victim = halloween_user(interaction.guild_id, mitglied.id)
        wait = 300 - (time.time() - float(thief.get("spook_at", 0)))
        if wait > 0:
            await interaction.response.send_message(f"🦇 Spook ist in {int(wait)} Sekunden wieder verfügbar.", ephemeral=True)
            return
        thief["spook_at"] = time.time()
        if random.random() < 0.65 and victim["sweets"] > 0:
            stolen = min(victim["sweets"], random.randint(5, 20))
            victim["sweets"] -= stolen
            thief["sweets"] += stolen
            result = f"👻 Du hast {sweets_amount(stolen)} von {mitglied.display_name} geklaut!"
        else:
            fine = min(thief["sweets"], random.randint(2, 8))
            thief["sweets"] -= fine
            result = f"🎃 Erwischt! Die Geister haben dir {sweets_amount(fine)} abgenommen."
        save()
        await interaction.response.send_message(result, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="halloween-shop", aliases=["hshop"])
    @commands.guild_only()
    async def halloween_shop_prefix(self, ctx):
        embed = discord.Embed(title="🎃 IronWolf · Halloween-Shop", description="Kaufe gruselige Sammlerstücke mit deinen gesammelten Süßigkeiten 🍬.", color=discord.Color.from_rgb(255, 119, 0))
        for name, item in HALLOWEEN_SHOP.items():
            embed.add_field(name=f"{item['description']} · {name}", value=f"Preis: **{sweets_amount(item['price'])}**", inline=False)
        embed.set_footer(text="Kaufen: !halloween-kaufen artikelname")
        await ctx.reply(embed=embed, mention_author=False)

    @app_commands.command(name="halloween-shop", description="Zeigt den Halloween-Shop für Süßigkeiten")
    @app_commands.guild_only()
    async def halloween_shop_slash(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🎃 IronWolf · Halloween-Shop", description="Gruselige Sammlerstücke, bezahlt mit Süßigkeiten 🍬.", color=discord.Color.from_rgb(255, 119, 0))
        for name, item in HALLOWEEN_SHOP.items():
            embed.add_field(name=f"{item['description']} · {name}", value=f"Preis: **{sweets_amount(item['price'])}**", inline=False)
        embed.set_footer(text="Kaufen: /halloween-kaufen")
        await interaction.response.send_message(embed=embed)

    @commands.command(name="halloween-kaufen", aliases=["hkauf"])
    @commands.guild_only()
    async def halloween_buy_prefix(self, ctx, *, artikel: str):
        name = artikel.strip().lower().replace(" ", "-")
        item = HALLOWEEN_SHOP.get(name)
        if not item:
            await ctx.reply("❌ Artikel nicht gefunden. Nutze !halloween-shop, z. B. !halloween-kaufen geistermaske.", mention_author=False)
            return
        u = halloween_user(ctx.guild.id, ctx.author.id)
        if u["sweets"] < item["price"]:
            await ctx.reply(f"🍬 Du brauchst {sweets_amount(item['price'])}, hast aber nur {sweets_amount(u['sweets'])}.", mention_author=False)
            return
        u["sweets"] -= item["price"]
        u["halloween_items"].append(name)
        save()
        await ctx.reply(f"🛍️ Gekauft: {item['description']} für {sweets_amount(item['price'])}! Übrig: {sweets_amount(u['sweets'])}.", mention_author=False)

    @app_commands.command(name="halloween-kaufen", description="Kaufe einen Artikel mit Halloween-Süßigkeiten")
    @app_commands.guild_only()
    async def halloween_buy_slash(self, interaction: discord.Interaction, artikel: str):
        name = artikel.strip().lower().replace(" ", "-")
        item = HALLOWEEN_SHOP.get(name)
        if not item:
            await interaction.response.send_message("❌ Artikel nicht gefunden. Nutze /halloween-shop.", ephemeral=True)
            return
        u = halloween_user(interaction.guild_id, interaction.user.id)
        if u["sweets"] < item["price"]:
            await interaction.response.send_message(f"🍬 Du brauchst {sweets_amount(item['price'])}, hast aber nur {sweets_amount(u['sweets'])}.", ephemeral=True)
            return
        u["sweets"] -= item["price"]
        u["halloween_items"].append(name)
        save()
        await interaction.response.send_message(f"🛍️ Gekauft: {item['description']} für {sweets_amount(item['price'])}! Übrig: {sweets_amount(u['sweets'])}.")

    @commands.command(name="halloween-guthaben", aliases=["sweets"])
    @commands.guild_only()
    async def halloween_balance_prefix(self, ctx):
        u = halloween_user(ctx.guild.id, ctx.author.id)
        items = ", ".join(u["halloween_items"]) if u["halloween_items"] else "Noch keine Halloween-Artikel"
        await ctx.reply(f"🍬 Süßigkeiten: **{sweets_amount(u['sweets'])}**\\n🎒 Halloween-Inventar: {items}", mention_author=False)

    @app_commands.command(name="halloween-guthaben", description="Zeigt Süßigkeiten und Halloween-Inventar")
    @app_commands.guild_only()
    async def halloween_balance_slash(self, interaction: discord.Interaction):
        u = halloween_user(interaction.guild_id, interaction.user.id)
        items = ", ".join(u["halloween_items"]) if u["halloween_items"] else "Noch keine Halloween-Artikel"
        await interaction.response.send_message(f"🍬 Süßigkeiten: **{sweets_amount(u['sweets'])}**\\n🎒 Halloween-Inventar: {items}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Economy(bot))
    # Persistent views: these buttons continue working after bot restarts.
    bot.add_view(BankView())
    bot.add_view(ShopView())
