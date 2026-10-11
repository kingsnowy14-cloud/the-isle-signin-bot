import os
import json
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone

# -----------------------------
# Bot-Konfiguration
# -----------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN env var fehlt.")

CONFIG_FILE = "translation_config.json"

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------------
# JSON Config
# -----------------------------
def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

config = load_config()

def guild_channel_langs(guild_id):
    return config.setdefault(str(guild_id), {})

def set_channel_languages(guild_id, channel_id, languages):
    guild_cfg = guild_channel_langs(guild_id)
    guild_cfg[str(channel_id)] = languages
    save_config(config)

def get_channel_languages(guild_id, channel_id):
    return guild_channel_langs(guild_id).get(str(channel_id), [])

def remove_channel_languages(guild_id, channel_id):
    guild_cfg = guild_channel_langs(guild_id)
    removed = guild_cfg.pop(str(channel_id), None)
    save_config(config)
    return removed

# -----------------------------
# Sprachen / Mapping
# -----------------------------
_LANGUAGE_ALIASES = {
    "german": "de", "deutsch": "de",
    "english": "en", "englisch": "en",
    "czech": "cs", "čeština": "cs", "cestina": "cs",
    "spanish": "es", "español": "es",
    "french": "fr", "français": "fr",
    "italian": "it",
    "portuguese": "pt",
    "dutch": "nl",
    "polish": "pl",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "russian": "ru",
    "ukrainian": "uk",
    "turkish": "tr",
    "arabic": "ar",
    "hindi": "hi",
    "swedish": "sv",
    "danish": "da",
    "norwegian": "nb", "norsk": "nb",
    "finnish": "fi",
    "greek": "el",
    "romanian": "ro",
    "hungarian": "hu",
    "indonesian": "id",
    "vietnamese": "vi",
    "thai": "th",
}

_ARGOS_TRANSLATION_LOCK = asyncio.Lock()

def normalize_translation_language(language: str) -> str:
    requested = (language or "").strip().lower()
    if not requested:
        raise ValueError("Bitte gib eine Zielsprache an.")

    code = _LANGUAGE_ALIASES.get(requested, requested.replace("_", "-").split("-")[0])
    if not code.isalpha() or len(code) not in (2, 3):
        raise ValueError(f"Unbekannte Sprache: {language}")
    return code

def _argos_translate_blocking(text: str, target_code: str) -> str:
    """
    Übersetzt mit Argos Translate.
    Wenn kein direktes Modell existiert, wird über Englisch als Pivot übersetzt.
    """
    import argostranslate.package
    import argostranslate.translate
    from langdetect import detect, LangDetectException

    try:
        source_code = detect(text)
    except LangDetectException:
        raise ValueError("Die Ausgangssprache konnte nicht erkannt werden. Bitte mehr Text eingeben.")

    if source_code == target_code:
        return text

    installed = argostranslate.translate.get_installed_languages()
    installed_codes = {lang.code for lang in installed}

    def has_pair(src: str, dst: str) -> bool:
        current = argostranslate.translate.get_installed_languages()
        src_lang = next((lang for lang in current if lang.code == src), None)
        dst_lang = next((lang for lang in current if lang.code == dst), None)
        return bool(src_lang and dst_lang and any(t.code == dst for t in src_lang.get_translation(dst_lang)))

    def install_pair(src: str, dst: str) -> bool:
        if has_pair(src, dst):
            return True

        argostranslate.package.update_package_index()
        available = argostranslate.package.get_available_packages()

        package = next(
            (item for item in available if item.from_code == src and item.to_code == dst),
            None
        )
        if package is None:
            return False

        path = package.download()
        argostranslate.package.install_from_path(path)
        return has_pair(src, dst)

    # Direktes Modell versuchen
    if not install_pair(source_code, target_code):
        # Über Englisch
        if source_code != "en" and not install_pair(source_code, "en"):
            raise ValueError(f"Argos Translate hat kein Modell von '{source_code}' nach Englisch.")
        if target_code != "en" and not install_pair("en", target_code):
            raise ValueError(f"Argos Translate hat kein Übersetzungsmodell für '{target_code}'.")

    installed = argostranslate.translate.get_installed_languages()
    source_lang = next((lang for lang in installed if lang.code == source_code), None)
    target_lang = next((lang for lang in installed if lang.code == target_code), None)

    if source_lang and target_lang:
        translations = source_lang.get_translation(target_lang)
        if translations:
            return translations[0].translate(text)

    # Fallback über Englisch
    english = next((lang for lang in installed if lang.code == "en"), None)
    if not source_lang or not english or not target_lang:
        raise ValueError("Die benötigten Argos-Sprachmodelle konnten nicht geladen werden.")

    first = source_lang.get_translation(english)
    second = english.get_translation(target_lang)
    if not first or not second:
        raise ValueError("Für diese Sprachkombination sind keine Argos-Modelle verfügbar.")

    return second[0].translate(first[0].translate(text))

async def translate_text(text: str, target_language: str) -> str:
    target_code = normalize_translation_language(target_language)
    async with _ARGOS_TRANSLATION_LOCK:
        return await asyncio.to_thread(_argos_translate_blocking, text, target_code)

# -----------------------------
# Hilfsfunktionen für Discord
# -----------------------------
def create_embed(*args, **kwargs):
    embed = discord.Embed(*args, **kwargs)
    embed.set_footer(text="Created by KingSnowy")
    return embed

# -----------------------------
# Slash Commands
# -----------------------------
@bot.tree.command(name="translate", description="Übersetze Text in eine beliebige Sprache")
@app_commands.describe(text="Text zum Übersetzen", sprache="Zielsprache, z. B. de, en, fr, ja")
async def translate_command(interaction: discord.Interaction, text: str, sprache: str):
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        result = await translate_text(text, sprache)
    except ImportError:
        await interaction.followup.send(
            "Die Übersetzungsbibliothek fehlt. Installiere `argostranslate` und `langdetect`.",
            ephemeral=True
        )
        return
    except Exception as exc:
        await interaction.followup.send(
            f"Übersetzung fehlgeschlagen. Prüfe die Sprache (z. B. `de`, `en`, `fr`, `ja`). Fehler: `{type(exc).__name__}`",
            ephemeral=True,
        )
        return

    if len(result) > 4000:
        result = result[:3997] + "…"

    embed = create_embed(
        title=f"🌐 Übersetzung · {discord.utils.escape_markdown(sprache)[:200]}",
        description=result,
        color=discord.Color.blurple()
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="channeltranslate", description="Übersetze neue Nachrichten in diesem Kanal automatisch")
@app_commands.describe(
    sprache1="Erste Zielsprache (benötigt)",
    sprache2="Zweite Zielsprache (optional)",
    sprache3="Dritte Zielsprache (optional)",
    sprache4="Vierte Zielsprache (optional)",
    sprache5="Fünfte Zielsprache (optional)",
)
async def channeltranslate(
    interaction: discord.Interaction,
    sprache1: str,
    sprache2: str = None,
    sprache3: str = None,
    sprache4: str = None,
    sprache5: str = None,
):
    if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Nutze diesen Befehl in einem normalen Textkanal.", ephemeral=True)
        return

    raw_languages = [x.strip() for x in [sprache1, sprache2, sprache3, sprache4, sprache5] if x and x.strip()]
    if not raw_languages:
        await interaction.response.send_message("Bitte gib mindestens eine Zielsprache an.", ephemeral=True)
        return

    resolved = []
    try:
        for language in raw_languages:
            code = normalize_translation_language(language)
            if code not in resolved:
                resolved.append(code)
    except Exception:
        await interaction.response.send_message(
            f"Unbekannte Sprache. Nutze z. B. `de`, `en`, `fr`, `es`, `ja` oder einen Namen wie `German`.",
            ephemeral=True,
        )
        return

    set_channel_languages(interaction.guild.id, interaction.channel.id, resolved)

    await interaction.response.send_message(
        f"Automatische Übersetzung für {interaction.channel.mention} aktiviert. "
        f"Zielsprachen: **{', '.join(resolved)}**\n"
        "Neue Nachrichten werden automatisch übersetzt. Mit `/channeltranslate-stop` kannst du es wieder deaktivieren.",
        ephemeral=True,
    )

@bot.tree.command(name="channeltranslate-stop", description="Deaktiviert die automatische Übersetzung in diesem Kanal")
async def channeltranslate_stop(interaction: discord.Interaction):
    if interaction.guild is None or interaction.channel is None:
        await interaction.response.send_message("Dieser Befehl funktioniert nur in einem Serverkanal.", ephemeral=True)
        return

    removed = remove_channel_languages(interaction.guild.id, interaction.channel.id)
    if removed:
        await interaction.response.send_message("Automatische Übersetzung in diesem Kanal wurde deaktiviert.", ephemeral=True)
    else:
        await interaction.response.send_message("Für diesen Kanal war keine automatische Übersetzung aktiviert.", ephemeral=True)

@bot.tree.command(name="help", description="Zeigt Hilfe zu den verfügbaren Befehlen")
async def help_command(interaction: discord.Interaction):
    embed = create_embed(
        title="📖 Hilfe",
        description="Verfügbare Befehle:\n\n`/translate` – Übersetze Text\n`/channeltranslate` – Automatische Übersetzung für einen Kanal\n`/channeltranslate-stop` – Deaktiviert automatische Übersetzung",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# -----------------------------
# Automatische Kanalübersetzung
# -----------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    if message.guild and isinstance(message.channel, discord.TextChannel):
        guild_id = message.guild.id
        channel_id = message.channel.id
        target_languages = get_channel_languages(guild_id, channel_id)

        if target_languages:
            text = (message.content or "").strip()
            if text and not text.startswith(("!", "/")):
                for target_language in target_languages[:5]:
                    try:
                        translated = await translate_text(text, target_language)
                        if translated and translated.strip():
                            translated_message = (
                                f"🌐 **Übersetzung ({target_language}) von {message.author.display_name}:**\n"
                                f"{translated[:1900]}"
                            )
                            await message.channel.send(
                                translated_message,
                                reference=message,
                                mention_author=False,
                                allowed_mentions=discord.AllowedMentions.none(),
                            )
                    except Exception as exc:
                        print(f"Auto-Translation failed for {target_language}: {type(exc).__name__}: {exc}")

    await bot.process_commands(message)

# -----------------------------
# READY Event
# -----------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands.")
    except Exception as exc:
        print(f"Could not sync slash commands: {type(exc).__name__}: {exc}")

# -----------------------------
# Start
# -----------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
