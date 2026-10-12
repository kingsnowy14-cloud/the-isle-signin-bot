"""IronWolf KI-Assistent für The Isle, RP und Community-Verwaltung.

Konfiguration über Railway-Variablen:
- OPENAI_API_KEY (erforderlich)
- OPENAI_MODEL (optional, Standard: gpt-4o-mini)
- OPENAI_BASE_URL (optional, OpenAI-kompatibler Chat-Completions-Endpunkt)
Der Assistent übermittelt ausschließlich den Text, den ein Nutzer direkt in einen KI-Befehl eingibt.
"""
import os
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
SYSTEM_PROMPT = (
    "Du bist IronWolf KI, ein hilfreicher deutschsprachiger Assistent für eine The-Isle-Community. "
    "Du unterstützt bei RP-Ideen, Dinosaurier-Charakteren, Events, verständlichen Regeltexten, "
    "neutralen Moderationsformulierungen und Community-Organisation. Antworte klar, praktisch und "
    "respektvoll. Erfinde keine angeblichen Serverregeln oder Fakten: frage nach, wenn wichtige Details "
    "fehlen. Bei Moderationsfällen bleib neutral, trenne belegte Angaben von Vermutungen und empfehle "
    "keine Bestrafung allein aufgrund unbelegter Behauptungen. Du kannst keine Discord-Aktionen selbst "
    "ausführen und sollst nicht behaupten, etwas gespeichert, geprüft oder veröffentlicht zu haben."
)


class IronWolfAI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def ask_ai(self, prompt: str, instruction: str = "") -> str:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "KI ist noch nicht eingerichtet. Hinterlege OPENAI_API_KEY in den Railway-Variablen "
                "und starte den Dienst neu."
            )
        endpoint = os.getenv("OPENAI_BASE_URL", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
        model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + (("\n\nZusatzauftrag: " + instruction) if instruction else "")},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 900,
        }
        timeout = aiohttp.ClientTimeout(total=35)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            ) as response:
                if response.status == 401:
                    raise RuntimeError("Der KI-API-Schlüssel wurde abgelehnt. Prüfe OPENAI_API_KEY.")
                if response.status == 429:
                    raise RuntimeError("Das KI-Limit wurde erreicht. Prüfe Guthaben und Rate-Limits des API-Anbieters.")
                if response.status >= 500:
                    raise RuntimeError(f"Der KI-Anbieter ist vorübergehend nicht erreichbar (HTTP {response.status}).")
                if response.status >= 400:
                    detail = (await response.text())[:300]
                    raise RuntimeError(f"Die KI-Anfrage wurde abgelehnt (HTTP {response.status}): {detail}")
                data = await response.json(content_type=None)
        try:
            answer = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("Der KI-Anbieter hat eine unerwartete Antwort geliefert.")
        if isinstance(answer, list):
            answer = "\n".join(str(part.get("text", "")) for part in answer if isinstance(part, dict))
        answer = str(answer).strip()
        if not answer:
            raise RuntimeError("Die KI hat eine leere Antwort zurückgegeben.")
        return answer

    async def respond(self, interaction: discord.Interaction, prompt: str, instruction: str = ""):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            answer = await self.ask_ai(prompt, instruction)
        except RuntimeError as exc:
            await interaction.followup.send(f"⚠️ {exc}", ephemeral=True)
            return
        except (aiohttp.ClientError, TimeoutError) as exc:
            await interaction.followup.send(
                f"⚠️ Die KI-Anfrage ist fehlgeschlagen ({type(exc).__name__}). Versuche es später erneut.",
                ephemeral=True,
            )
            return
        # Discord message content limit is 2000 characters.
        chunks = [answer[i:i + 1900] for i in range(0, len(answer), 1900)]
        for index, chunk in enumerate(chunks[:4]):
            prefix = "🤖 **IronWolf KI**\n" if index == 0 else ""
            await interaction.followup.send(prefix + chunk, ephemeral=True)

    @app_commands.command(name="ki", description="Stelle IronWolf KI eine Frage oder bitte um Hilfe")
    @app_commands.guild_only()
    @app_commands.describe(prompt="Deine Frage, Idee oder Aufgabe (max. 1500 Zeichen)")
    async def ki(self, interaction: discord.Interaction, prompt: app_commands.Range[str, 1, 1500]):
        await self.respond(interaction, prompt)

    @app_commands.command(name="ki-eventidee", description="Erstellt eine passende The-Isle-/RP-Eventidee")
    @app_commands.guild_only()
    @app_commands.describe(thema="Thema, Dinosaurier oder gewünschte Stimmung", details="Optionale Einschränkungen oder Zielgruppe")
    async def ki_eventidee(
        self,
        interaction: discord.Interaction,
        thema: app_commands.Range[str, 1, 250],
        details: str = "",
    ):
        prompt = f"Thema: {thema}\nZusatzdetails: {details or 'Keine vorgegeben'}"
        await self.respond(
            interaction, prompt,
            "Erstelle eine umsetzbare Eventidee mit Titel, kurzer Story, Ablauf in 3–5 Schritten, "
            "benötigten Rollen/Organisatoren und fairen Regeln. Markiere optionale Vorschläge klar.",
        )

    @app_commands.command(name="ki-rp-charakter", description="Entwirft einen spielbaren Dinosaurier-RP-Charakter")
    @app_commands.guild_only()
    @app_commands.describe(dinosaurier="Dinosaurierart", stil="Gewünschter Charakterstil oder Hintergrund")
    async def ki_rp_charakter(
        self,
        interaction: discord.Interaction,
        dinosaurier: app_commands.Range[str, 1, 100],
        stil: app_commands.Range[str, 1, 500] = "Realistisches, lore-freundliches RP",
    ):
        await self.respond(
            interaction, f"Dinosaurier: {dinosaurier}\nStil: {stil}",
            "Erstelle einen glaubwürdigen Charakter mit Persönlichkeit, Stärken, Schwächen, Motivation, "
            "Spielzielen und drei konkreten RP-Aufhängern. Behaupte keine serverindividuellen Regeln.",
        )

    @app_commands.command(name="ki-moderationshilfe", description="Formuliert einen neutralen Moderations- oder Regeltext")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(fall="Sachverhalt ohne unnötige private Daten", ziel="Gewünschtes Ergebnis oder Textformat")
    async def ki_moderationshilfe(
        self,
        interaction: discord.Interaction,
        fall: app_commands.Range[str, 1, 1200],
        ziel: app_commands.Range[str, 1, 300] = "Neutrale Nachricht mit klaren nächsten Schritten",
    ):
        await self.respond(
            interaction, f"Sachverhalt: {fall}\nGewünschtes Ergebnis: {ziel}",
            "Hilf bei der neutralen Formulierung. Trenne bekannte Fakten und offene Fragen. "
            "Schlage keine automatische Sanktion vor und weise auf fehlende Informationen hin. "
            "Gib einen direkt nutzbaren Entwurf und ggf. eine kurze Checkliste.",
        )


async def setup(bot):
    await bot.add_cog(IronWolfAI(bot))
