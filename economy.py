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


DISABLED_GUILDS = {1561027077984227371}


async def silently_ignore_disabled_guild(interaction: discord.Interaction) -> bool:
    """Acknowledge and immediately remove interactions from disabled servers."""
    if interaction.guild_id not in DISABLED_GUILDS:
        return False
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        await interaction.delete_original_response()
    except discord.HTTPException:
        pass
    return True


class BankView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="View my account", style=discord.ButtonStyle.primary, emoji="🏦", custom_id="ironwolf:bank:account")
    async def show_account(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await silently_ignore_disabled_guild(interaction):
            return
        if interaction.guild_id is None:
            await interaction.response.send_message("Only available in a server.", ephemeral=True)
            return
        u = user(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            f"🏦 Bank account: **{money(u['bank'])}**\n💶 Cash: **{money(u['cash'])}**",
            ephemeral=True
        )


class ShopPurchaseView(discord.ui.View):
    def __init__(self, guild_id: int, items: dict):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.items = dict(items)
        self.selected_name = None
        options = [
            discord.SelectOption(label=name[:100], description=f"{money(item['price'])} · {(item.get('description') or 'No description')[:70]}", value=name)
            for name, item in list(items.items())[:25]
        ]
        self.add_item(ShopItemSelect(options, self))
        self.add_item(ShopBuyButton(self))


class ShopItemSelect(discord.ui.Select):
    def __init__(self, options, parent_view):
        super().__init__(placeholder="Select an item to purchase…", min_values=1, max_values=1, options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if await silently_ignore_disabled_guild(interaction):
            return
        self.parent_view.selected_name = self.values[0]
        item = self.parent_view.items.get(self.values[0])
        if item:
            await interaction.response.send_message(
                f"Selected: **{self.values[0]}** for **{money(item['price'])}**. Then press “Buy Now”.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("This item is no longer available.", ephemeral=True)


class ShopBuyButton(discord.ui.Button):
    def __init__(self, parent_view):
        super().__init__(label="Buy now", style=discord.ButtonStyle.success, emoji="🛍️")
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if await silently_ignore_disabled_guild(interaction):
            return
        if interaction.guild_id != self.parent_view.guild_id:
            await interaction.response.send_message("This shop belongs to a different server.", ephemeral=True)
            return
        name = self.parent_view.selected_name
        if not name:
            await interaction.response.send_message("Select an item above first.", ephemeral=True)
            return
        g = guild(interaction.guild_id)
        item = self.parent_view.items.get(name)
        if not item:
            await interaction.response.send_message("Item not found. Open /shop again.", ephemeral=True)
            return
        u = user(interaction.guild_id, interaction.user.id)
        price = int(item["price"])
        if u["bank"] < price:
            await interaction.response.send_message(f"Not enough money in your bank account. Price: **{money(price)}**.", ephemeral=True)
            return
        u["bank"] -= price
        u["items"].append(name)
        save()
        await interaction.response.send_message(
            f"🛍️ You purchased **{name}** for **{money(price)}**. New balance: **{money(u['bank'])}**.",
            ephemeral=True
        )


class ShopView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Buy item", style=discord.ButtonStyle.success, emoji="🛒", custom_id="ironwolf:shop:buy")
    async def open_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await silently_ignore_disabled_guild(interaction):
            return
        if interaction.guild_id is None:
            await interaction.response.send_message("Only available in a server.", ephemeral=True)
            return
        items = dict(guild(interaction.guild_id)["shop"])
        if not items:
            await interaction.response.send_message("Der Shop ist noch leer.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Select an item, then click **Buy now**:",
            view=ShopPurchaseView(interaction.guild_id, items),
            ephemeral=True
        )


def admin():
    return app_commands.checks.has_permissions(manage_guild=True)

class Economy(commands.Cog):
    DISABLED_GUILDS = DISABLED_GUILDS

    async def cog_check(self, ctx):
        # Suppress all economy prefix commands without sending a bot message.
        return not (ctx.guild and ctx.guild.id in self.DISABLED_GUILDS)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Slash commands must be acknowledged by Discord, then the empty
        # ephemeral response is immediately deleted so users see no bot reply.
        return not await silently_ignore_disabled_guild(interaction)
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="work")
    @commands.guild_only()
    async def work(self, ctx):
        g, u = guild(ctx.guild.id), user(ctx.guild.id, ctx.author.id)
        wait = 60 - (time.time() - u["work_at"])
        if wait > 0:
            await ctx.reply(f"You can work again in {int(wait)} seconds.", mention_author=False); return
        gross = random.randint(120, 350)
        actual, tax, capped = credit(g, u, gross)
        u["work_at"] = time.time(); save()
        extra = f" Steuer: {money(tax)}." if tax else ""
        extra += f" Wegen des Banklimits nicht gutgeschrieben: {money(capped)}." if capped else ""
        await ctx.reply(f"🛠️ You earned {money(actual)} and it was deposited directly into your bank account.{extra}\nBalance: **{money(u['bank'])}**", mention_author=False)

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
            result = f"💸 Heist successful! {money(actual)} was deposited into your bank account."
            if tax: result += f" Steuer: {money(tax)}."
            if capped: result += f" Wegen des Banklimits nicht gutgeschrieben: {money(capped)}."
        else:
            loss = random.randint(100, 300)
            lost = min(u["bank"], loss); u["bank"] -= lost
            result = f"🚔 You were caught and lost {money(lost)} verloren."
        save()
        await ctx.reply(f"{result}\n🏦 Balance: **{money(u['bank'])}**", mention_author=False)

    @commands.command(name="pay")
    @commands.guild_only()
    async def pay(self, ctx, member: discord.Member = None, amount: int = None):
        if member is None or amount is None:
            await ctx.reply("Verwendung: !pay @Mitglied Betrag", mention_author=False); return
        if member.bot or member.id == ctx.author.id or amount <= 0:
            await ctx.reply("Choose another member and an amount greater than 0.", mention_author=False); return
        sender, receiver = user(ctx.guild.id, ctx.author.id), user(ctx.guild.id, member.id)
        if sender["bank"] < amount:
            await ctx.reply("You do not have enough money in your bank account.", mention_author=False); return
        sender["bank"] -= amount; receiver["bank"] += amount; save()
        await ctx.reply(f"💸 {ctx.author.mention} hat {member.mention} **{money(amount)}** überwiesen.", mention_author=False)

    @app_commands.command(name="bank", description="Public bank overview and your account balance")
    async def bank_overview(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("Only available in a server.", ephemeral=True); return
        g, u = guild(interaction.guild_id), user(interaction.guild_id, interaction.user.id)
        e = discord.Embed(title="🏦 IronWolf Bank", description="Public bank overview. Use /account for your private balance.", color=discord.Color.blurple())
        e.add_field(name="Zinsen", value=f"{g['bank_interest']:.2f} %", inline=True)
        e.add_field(name="Steuern", value=f"{'AN' if g['tax_enabled'] else 'AUS'} · {g['tax_percent']:.1f} %", inline=True)
        e.add_field(name="Banklimit", value=money(g["bank_limit"]), inline=True)
        await interaction.response.send_message(embed=e, view=BankView(), ephemeral=False)

    @app_commands.command(name="account", description="Privately displays your account balance")
    async def account(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await interaction.response.send_message("Only available in a server.", ephemeral=True); return
        u = user(interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(f"🏦 Bank account: **{money(u['bank'])}**\n💶 Cash: **{money(u['cash'])}**", ephemeral=True)

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
            await interaction.response.send_message("Only available in a server.", ephemeral=True); return
        items = guild(interaction.guild_id)["shop"]
        e = discord.Embed(title="🛒 IronWolf-Shop", description="Artikel und Preise für alle Mitglieder", color=discord.Color.gold())
        if not items:
            e.description = "Der Shop ist noch leer. Admins können /shop-hinzufuegen verwenden."
        else:
            for name, item in list(items.items())[:25]:
                e.add_field(name=name[:256], value=f"Preis: **{money(item['price'])}**\n{item.get('description') or 'No description'}", inline=True)
        e.set_footer(text="Kaufen: /shop-kaufen")
        await interaction.response.send_message(embed=e, view=ShopView(), ephemeral=False)

    @app_commands.command(name="shop-hinzufuegen", description="Shopartikel hinzufügen (Admin)")
    @admin()
    async def shop_add(self, interaction: discord.Interaction, name: str, preis: app_commands.Range[int, 1, 100000000], beschreibung: str = ""):
        name = name.strip()[:50]
        if not name:
            await interaction.response.send_message("Please enter an item name.", ephemeral=True); return
        guild(interaction.guild_id)["shop"][name.lower()] = {"price": preis, "description": beschreibung[:150]}; save()
        await interaction.response.send_message(f"Artikel **{name}** for **{money(preis)}** hinzugefügt.", ephemeral=True)

    @app_commands.command(name="shop-entfernen", description="Shopartikel entfernen (Admin)")
    @admin()
    async def shop_remove(self, interaction: discord.Interaction, name: str):
        item = guild(interaction.guild_id)["shop"].pop(name.lower(), None); save()
        await interaction.response.send_message("Item removed." if item else "Item not found.", ephemeral=True)

    @app_commands.command(name="shop-kaufen", description="Shopartikel kaufen")
    async def shop_buy(self, interaction: discord.Interaction, name: str):
        if interaction.guild_id is None:
            await interaction.response.send_message("Only available in a server.", ephemeral=True); return
        g, u = guild(interaction.guild_id), user(interaction.guild_id, interaction.user.id)
        item = g["shop"].get(name.lower())
        if not item:
            await interaction.response.send_message("Item not found. Use /shop.", ephemeral=True); return
        if u["bank"] < item["price"]:
            await interaction.response.send_message("You do not have enough money in your bank account.", ephemeral=True); return
        u["bank"] -= item["price"]; u["items"].append(name.lower()); save()
        await interaction.response.send_message(f"🛍️ **{name}** for **{money(item['price'])}** gekauft.", ephemeral=True)


async def setup(bot):
    # Register economy slash commands per server, not globally. Discord limits
    # global application commands to 100, and IronWolf already uses most of them.
    # The extension is loaded after on_ready, so bot.guilds is populated here.
    guilds = list(bot.guilds)
    if guilds:
        await bot.add_cog(Economy(bot), guilds=guilds)
        print(f"[ECONOMY] Registered economy commands for {len(guilds)} guild(s).", flush=True)
    else:
        # Prefix commands and views can still load if the bot has no guild cache.
        await bot.add_cog(Economy(bot))
        print("[ECONOMY] No guilds cached; slash commands may need guild registration.", flush=True)
    # Persistent views: these buttons continue working after bot restarts.
    bot.add_view(BankView())
    bot.add_view(ShopView())