"""Standalone Discord music bot: Spotify metadata + operator-authorized local audio."""
import asyncio
import os
import re
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
AUDIO_LIBRARY_DIR = Path(os.getenv("AUDIO_LIBRARY_DIR", "audio_library"))
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt.")
if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
    raise RuntimeError("SPOTIFY_CLIENT_ID und SPOTIFY_CLIENT_SECRET fehlen.")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET))
guild_queues: dict[int, asyncio.Queue] = {}
guild_workers: dict[int, asyncio.Task] = {}
guild_current: dict[int, dict] = {}


def normalize(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-z0-9äöüß]+", " ", value)
    return " ".join(value.split())


def authorized_audio_for(title: str, artist: str) -> Optional[Path]:
    """Find a local file the operator is authorized to play; never downloads audio."""
    if not AUDIO_LIBRARY_DIR.exists():
        return None
    wanted = normalize(f"{artist} {title}")
    wanted_alt = normalize(f"{title} {artist}")
    extensions = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}
    for path in AUDIO_LIBRARY_DIR.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        stem = normalize(path.stem.replace(" - ", " "))
        if stem in {wanted, wanted_alt} or (normalize(title) in stem and normalize(artist) in stem):
            return path
    return None


async def playback_worker(guild_id: int):
    queue = guild_queues[guild_id]
    while True:
        item = await queue.get()
        try:
            guild = bot.get_guild(guild_id)
            voice = guild.voice_client if guild else None
            if not voice or not voice.is_connected():
                continue
            source = discord.FFmpegPCMAudio(str(item["path"]), executable=FFMPEG_PATH, before_options="-nostdin", options="-vn")
            finished = asyncio.Event()
            def after_play(error):
                if error:
                    print(f"Playback error: {error}")
                bot.loop.call_soon_threadsafe(finished.set)
            guild_current[guild_id] = item
            voice.play(source, after=after_play)
            await finished.wait()
        except Exception as exc:
            print(f"Music worker error in guild {guild_id}: {exc}")
        finally:
            queue.task_done()
            if queue.empty():
                guild_current.pop(guild_id, None)


async def ensure_voice(interaction: discord.Interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        raise app_commands.AppCommandError("Dieser Befehl funktioniert nur auf einem Server.")
    if not interaction.user.voice or not interaction.user.voice.channel:
        raise app_commands.AppCommandError("Bitte tritt zuerst einem Sprachkanal bei.")
    voice = interaction.guild.voice_client
    if voice and voice.is_connected():
        if voice.channel != interaction.user.voice.channel:
            await voice.move_to(interaction.user.voice.channel)
    else:
        await interaction.user.voice.channel.connect()
    return interaction.guild.voice_client


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Angemeldet als {bot.user}; {len(synced)} Slash-Commands synchronisiert.")
    except Exception as exc:
        print(f"Slash-Command-Sync fehlgeschlagen: {exc}")


@bot.tree.command(name="play", description="Spotify-Titel suchen und passende lokale Audiodatei abspielen.")
@app_commands.describe(song="Songtitel, Interpret oder Spotify-Track-Link")
async def play(interaction: discord.Interaction, song: str):
    await interaction.response.defer(thinking=True)
    try:
        await ensure_voice(interaction)
        match = re.search(r"open\.spotify\.com/track/([A-Za-z0-9]+)", song)
        if match:
            track = await asyncio.to_thread(spotify.track, match.group(1))
            tracks = [track]
        else:
            result = await asyncio.to_thread(spotify.search, q=song, type="track", limit=5)
            tracks = result.get("tracks", {}).get("items", [])
        if not tracks:
            await interaction.followup.send("Kein passender Spotify-Titel gefunden.")
            return
        track = tracks[0]
        title = track["name"]
        artists = ", ".join(a["name"] for a in track.get("artists", []))
        audio_path = authorized_audio_for(title, artists)
        if not audio_path:
            await interaction.followup.send(
                f"Spotify-Treffer: **{discord.utils.escape_markdown(title)} — {discord.utils.escape_markdown(artists)}**\n"
                f"[Auf Spotify ansehen]({track['external_urls']['spotify']})\n"
                "Keine passende Datei in der lokalen, autorisierten Musikbibliothek gefunden. "
                "Lege eine Datei, die du rechtmäßig abspielen darfst, in AUDIO_LIBRARY_DIR ab; "
                f"Dateiname z. B. {artists} - {title}.mp3"
            )
            return
        gid = interaction.guild_id
        if gid not in guild_queues:
            guild_queues[gid] = asyncio.Queue()
        task = guild_workers.get(gid)
        if task is None or task.done():
            guild_workers[gid] = asyncio.create_task(playback_worker(gid))
        await guild_queues[gid].put({"title": title, "artist": artists, "path": audio_path, "spotify_url": track["external_urls"]["spotify"]})
        await interaction.followup.send(f"🎵 Zur Warteschlange hinzugefügt: **{title} — {artists}**\n[Spotify-Link]({track['external_urls']['spotify']})")
    except Exception as exc:
        await interaction.followup.send(f"Musik konnte nicht gestartet werden: {exc}", ephemeral=True)


@bot.tree.command(name="queue", description="Zeigt aktuellen Titel und Warteschlange.")
async def queue_command(interaction: discord.Interaction):
    gid = interaction.guild_id
    current = guild_current.get(gid) if gid else None
    queue = guild_queues.get(gid) if gid else None
    lines = [f"▶️ Jetzt: **{current['title']} — {current['artist']}**"] if current else []
    if queue and not queue.empty():
        lines += [f"{i}. {x['title']} — {x['artist']}" for i, x in enumerate(list(queue._queue)[:10], 1)]
    await interaction.response.send_message("\n".join(lines) if lines else "Die Warteschlange ist leer.")


@bot.tree.command(name="skip", description="Überspringt den aktuellen Titel.")
async def skip(interaction: discord.Interaction):
    voice = interaction.guild.voice_client if interaction.guild else None
    if not voice or not voice.is_playing():
        await interaction.response.send_message("Aktuell läuft keine Musik.", ephemeral=True)
        return
    voice.stop()
    await interaction.response.send_message("⏭️ Titel übersprungen.")


@bot.tree.command(name="pause", description="Pausiert die Wiedergabe.")
async def pause(interaction: discord.Interaction):
    voice = interaction.guild.voice_client if interaction.guild else None
    if not voice or not voice.is_playing():
        await interaction.response.send_message("Aktuell läuft keine Musik.", ephemeral=True)
        return
    voice.pause()
    await interaction.response.send_message("⏸️ Pausiert.")


@bot.tree.command(name="resume", description="Setzt die Wiedergabe fort.")
async def resume(interaction: discord.Interaction):
    voice = interaction.guild.voice_client if interaction.guild else None
    if not voice or not voice.is_paused():
        await interaction.response.send_message("Die Wiedergabe ist nicht pausiert.", ephemeral=True)
        return
    voice.resume()
    await interaction.response.send_message("▶️ Fortgesetzt.")


@bot.tree.command(name="stop", description="Stoppt Musik, leert die Warteschlange und trennt den Bot.")
async def stop(interaction: discord.Interaction):
    gid = interaction.guild_id
    voice = interaction.guild.voice_client if interaction.guild else None
    queue = guild_queues.get(gid) if gid else None
    if queue:
        while not queue.empty():
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                break
    if voice:
        voice.stop()
        await voice.disconnect()
    if gid:
        guild_current.pop(gid, None)
    await interaction.response.send_message("⏹️ Gestoppt, Warteschlange geleert.")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    message = str(error) or "Unbekannter Fehler."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


bot.run(TOKEN)
