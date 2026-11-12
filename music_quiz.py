"""IronWolf Guess the Song: voice snippets, dropdown answers and persistent scores.

Moderators populate the library with audio files they own or are licensed to use.
Audio and scores are stored on the Railway volume via DATA_DIR.
"""
import asyncio
import random
import sqlite3
import uuid
from pathlib import Path

import discord
from discord.ext import commands

from storage import DATA_DIR

MUSIC_DIR = DATA_DIR / "musicquiz"
MUSIC_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".webm"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
CLIP_SECONDS = 12
ANSWER_SECONDS = 20
DEFAULT_ROUNDS = 10


def setup_music_quiz(bot: commands.Bot, db: sqlite3.Connection) -> None:
    """Register the prefix-command game without consuming global slash-command slots."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS musicquiz_songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            file_path TEXT NOT NULL UNIQUE,
            added_by INTEGER NOT NULL,
            added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS musicquiz_scores (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            points INTEGER NOT NULL DEFAULT 0,
            correct_answers INTEGER NOT NULL DEFAULT 0,
            games_played INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )"""
    )
    db.commit()

    active_games = {}

    class SongAnswerSelect(discord.ui.Select):
        def __init__(self, choices, correct_song_id):
            self.correct_song_id = int(correct_song_id)
            self.answers = {}
            options = [
                discord.SelectOption(label=f"{song[1]} — {song[2]}"[:100], value=str(song[0]))
                for song in choices
            ]
            super().__init__(
                placeholder="Welches Lied ist das? Wähle deine Antwort …",
                min_values=1,
                max_values=1,
                options=options,
                custom_id=f"ironwolf:musicquiz:{uuid.uuid4().hex[:20]}",
            )

        async def callback(self, interaction: discord.Interaction):
            user_id = interaction.user.id
            if user_id in self.answers:
                await interaction.response.send_message(
                    "Du hast für diese Runde bereits geantwortet.", ephemeral=True
                )
                return
            selected_id = int(self.values[0])
            self.answers[user_id] = {
                "correct": selected_id == self.correct_song_id,
                "answered_at": asyncio.get_running_loop().time(),
            }
            await interaction.response.send_message(
                "🎵 Antwort gespeichert! Das Ergebnis gibt es nach Ablauf der Runde.",
                ephemeral=True,
            )

    class SongAnswerView(discord.ui.View):
        def __init__(self, choices, correct_song_id):
            super().__init__(timeout=ANSWER_SECONDS)
            self.answer_select = SongAnswerSelect(choices, correct_song_id)
            self.add_item(self.answer_select)

        @property
        def answers(self):
            return self.answer_select.answers

        def disable_all(self):
            for item in self.children:
                item.disabled = True

    @bot.group(name="musicquiz", aliases=["mq", "guesssong"], invoke_without_command=True)
    @commands.guild_only()
    async def musicquiz(ctx):
        embed = discord.Embed(
            title="🐺 IronWolf · Guess the Song",
            description=(
                "musicquiz start [runden] – Spiel starten (du musst im Voice-Channel sein)\n"
                "musicquiz add Titel | Interpret – Audiodatei als Anhang hinzufügen\n"
                "musicquiz songs – verfügbare Songs anzeigen\n"
                "musicquiz remove ID – Song aus der Bibliothek entfernen\n"
                "musicquiz leaderboard – Top 10 anzeigen\n"
                "musicquiz stop – laufendes Spiel beenden"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="IronWolf · Nur Musik verwenden, für die ihr die nötigen Rechte habt.")
        await ctx.send(embed=embed)

    @musicquiz.command(name="add")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def add_song(ctx, *, metadata: str):
        if not ctx.message.attachments:
            await ctx.reply(
                "Bitte hänge eine Audiodatei an. Format: musicquiz add Titel | Interpret",
                mention_author=False,
            )
            return
        parts = [part.strip() for part in metadata.split("|", 1)]
        if len(parts) != 2 or not all(parts):
            await ctx.reply(
                "Bitte nutze das Format musicquiz add Titel | Interpret und hänge die Audiodatei an.",
                mention_author=False,
            )
            return
        attachment = ctx.message.attachments[0]
        suffix = Path(attachment.filename).suffix.lower()
        if suffix not in AUDIO_EXTENSIONS:
            await ctx.reply(
                "Nicht unterstütztes Format. Erlaubt sind MP3, WAV, OGG, M4A, FLAC und WEBM.",
                mention_author=False,
            )
            return
        if attachment.size > MAX_UPLOAD_BYTES:
            await ctx.reply("Die Datei darf höchstens 20 MB groß sein.", mention_author=False)
            return

        stored_name = f"{uuid.uuid4().hex}{suffix}"
        destination = MUSIC_DIR / stored_name
        try:
            await attachment.save(destination)
            db.execute(
                "INSERT INTO musicquiz_songs(guild_id, title, artist, file_path, added_by) VALUES (?, ?, ?, ?, ?)",
                (ctx.guild.id, parts[0][:90], parts[1][:90], str(destination), ctx.author.id),
            )
            db.commit()
        except Exception as exc:
            destination.unlink(missing_ok=True)
            print(f"[MUSIC QUIZ] Could not import audio: {type(exc).__name__}: {exc}", flush=True)
            await ctx.reply("Die Audiodatei konnte nicht gespeichert werden. Bitte Logs prüfen.", mention_author=False)
            return
        await ctx.reply(
            f"{discord.utils.escape_markdown(parts[0])} von {discord.utils.escape_markdown(parts[1])} wurde zur Songbibliothek hinzugefügt.",
            mention_author=False,
        )

    @musicquiz.command(name="songs")
    @commands.guild_only()
    async def list_songs(ctx):
        songs = db.execute(
            "SELECT id, title, artist FROM musicquiz_songs WHERE guild_id = ? ORDER BY title COLLATE NOCASE LIMIT 30",
            (ctx.guild.id,),
        ).fetchall()
        if not songs:
            await ctx.reply("Die Songbibliothek ist noch leer. Ein Moderator kann Songs mit musicquiz add hinzufügen.", mention_author=False)
            return
        lines = [f"ID {song[0]} · {discord.utils.escape_markdown(song[1])} — {discord.utils.escape_markdown(song[2])}" for song in songs]
        await ctx.reply(embed=discord.Embed(
            title=f"🎵 IronWolf · Songbibliothek ({len(songs)})",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        ), mention_author=False)

    @musicquiz.command(name="remove")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def remove_song(ctx, song_id: int):
        row = db.execute(
            "SELECT file_path, title FROM musicquiz_songs WHERE id = ? AND guild_id = ?", (song_id, ctx.guild.id)
        ).fetchone()
        if row is None:
            await ctx.reply("Diese Song-ID wurde nicht gefunden.", mention_author=False)
            return
        db.execute("DELETE FROM musicquiz_songs WHERE id = ? AND guild_id = ?", (song_id, ctx.guild.id))
        db.commit()
        Path(row[0]).unlink(missing_ok=True)
        await ctx.reply(f"{discord.utils.escape_markdown(row[1])} wurde entfernt.", mention_author=False)

    @musicquiz.command(name="leaderboard")
    @commands.guild_only()
    async def leaderboard(ctx):
        rows = db.execute(
            """SELECT user_id, points, correct_answers, games_played
               FROM musicquiz_scores WHERE guild_id = ?
               ORDER BY points DESC, correct_answers DESC LIMIT 10""",
            (ctx.guild.id,),
        ).fetchall()
        if not rows:
            await ctx.reply("Noch keine Punkte vorhanden. Starte mit musicquiz start das erste Quiz!", mention_author=False)
            return
        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for index, row in enumerate(rows):
            prefix = medals[index] if index < 3 else f"#{index + 1}"
            lines.append(f"{prefix} <@{row[0]}> — {row[1]} Punkte · {row[2]} richtige Antworten")
        embed = discord.Embed(
            title="🏆 IronWolf · Guess the Song Rangliste",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="Punkte werden dauerhaft auf dem IronWolf-Server gespeichert.")
        await ctx.reply(embed=embed, mention_author=False, allowed_mentions=discord.AllowedMentions.none())

    @musicquiz.command(name="stop")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def stop_game(ctx):
        session = active_games.get(ctx.guild.id)
        if not session:
            await ctx.reply("Aktuell läuft kein Music Quiz.", mention_author=False)
            return
        session["stopped"] = True
        voice = session.get("voice")
        if voice and voice.is_playing():
            voice.stop()
        task = session.get("task")
        if task and task is not asyncio.current_task():
            task.cancel()
        await ctx.reply("Das Music Quiz wird beendet.", mention_author=False)

    @musicquiz.command(name="start")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def start_game(ctx, rounds: int = DEFAULT_ROUNDS):
        guild_id = ctx.guild.id
        if guild_id in active_games:
            await ctx.reply("In diesem Server läuft bereits ein Music Quiz.", mention_author=False)
            return
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.reply("Du musst dich zuerst in einem Voice-Channel befinden.", mention_author=False)
            return
        if not 1 <= rounds <= 20:
            await ctx.reply("Bitte wähle zwischen 1 und 20 Runden.", mention_author=False)
            return

        songs = db.execute(
            "SELECT id, title, artist, file_path FROM musicquiz_songs WHERE guild_id = ?",
            (guild_id,),
        ).fetchall()
        songs = [song for song in songs if Path(song[3]).is_file()]
        if len(songs) < 4:
            await ctx.reply(
                f"Die Songbibliothek enthält nur {len(songs)} gültige Songs. Für das Dropdown werden mindestens 4 benötigt. Moderatoren können Dateien mit musicquiz add Titel | Interpret hinzufügen.",
                mention_author=False,
            )
            return

        voice_channel = ctx.author.voice.channel
        permissions = voice_channel.permissions_for(ctx.guild.me)
        if not permissions.connect or not permissions.speak:
            await ctx.reply("Mir fehlen die Berechtigungen Verbinden und/oder Sprechen in diesem Voice-Channel.", mention_author=False)
            return

        voice = ctx.guild.voice_client
        try:
            if voice is None:
                voice = await voice_channel.connect()
            elif voice.channel != voice_channel:
                await voice.move_to(voice_channel)
        except (discord.Forbidden, discord.ClientException, discord.HTTPException) as exc:
            await ctx.reply(
                f"Ich konnte dem Voice-Channel nicht beitreten: {type(exc).__name__}.",
                mention_author=False,
            )
            return

        session = {
            "task": asyncio.current_task(),
            "voice": voice,
            "stopped": False,
            "points": {},
            "participants": set(),
        }
        active_games[guild_id] = session
        await ctx.send(
            embed=discord.Embed(
                title="🎵 IronWolf · Guess the Song",
                description=f"Das Quiz startet mit {rounds} Runden. Höre genau hin und wähle den Song im Dropdown!",
                color=discord.Color.blurple(),
            )
        )

        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            ffmpeg_exe = get_ffmpeg_exe()
            loop = asyncio.get_running_loop()
            for round_number in range(1, rounds + 1):
                if session["stopped"]:
                    break
                correct = random.choice(songs)
                distractors = random.sample(
                    [song for song in songs if song[0] != correct[0]],
                    k=min(5, len(songs) - 1),
                )
                choices = distractors + [correct]
                random.shuffle(choices)
                view = SongAnswerView(choices, correct[0])
                round_started = loop.time()
                embed = discord.Embed(
                    title=f"🎧 Runde {round_number}/{rounds}",
                    description=(
                        f"Welches Lied hörst du?\nDer Ausschnitt läuft {CLIP_SECONDS} Sekunden. "
                        f"Du hast insgesamt {ANSWER_SECONDS} Sekunden zum Antworten."
                    ),
                    color=discord.Color.blurple(),
                )
                embed.set_footer(text="IronWolf Music Quiz · Jede Person hat einen Versuch pro Runde.")
                message = await ctx.send(embed=embed, view=view)
                try:
                    source = discord.FFmpegPCMAudio(
                        correct[3],
                        executable=ffmpeg_exe,
                        options=f"-vn -t {CLIP_SECONDS}",
                    )
                    if voice.is_playing():
                        voice.stop()
                    voice.play(source)
                except Exception as exc:
                    print(f"[MUSIC QUIZ] Audio playback failed for song {correct[0]}: {type(exc).__name__}: {exc}", flush=True)
                    view.stop()
                    view.disable_all()
                    await message.edit(
                        embed=discord.Embed(
                            title="Audio konnte nicht abgespielt werden",
                            description="Diese Runde wurde übersprungen. Bitte prüft FFmpeg, PyNaCl und die Audiodatei.",
                            color=discord.Color.orange(),
                        ),
                        view=view,
                    )
                    await asyncio.sleep(2)
                    continue

                await asyncio.sleep(CLIP_SECONDS)
                if voice.is_playing():
                    voice.stop()
                await asyncio.sleep(max(0, ANSWER_SECONDS - CLIP_SECONDS))
                view.stop()
                view.disable_all()

                round_lines = []
                for user_id, answer in view.answers.items():
                    session["participants"].add(user_id)
                    if answer["correct"]:
                        elapsed = answer["answered_at"] - round_started
                        points = 10 if elapsed <= 10 else 5
                        db.execute(
                            """INSERT INTO musicquiz_scores(guild_id, user_id, points, correct_answers, games_played)
                               VALUES (?, ?, ?, 1, 0)
                               ON CONFLICT(guild_id, user_id) DO UPDATE SET
                               points = points + excluded.points,
                               correct_answers = correct_answers + 1""",
                            (guild_id, user_id, points),
                        )
                        session["points"][user_id] = session["points"].get(user_id, 0) + points
                        round_lines.append(f"Richtig: <@{user_id}> +{points} Punkte")
                    else:
                        round_lines.append(f"Falsch: <@{user_id}>")

                db.commit()
                result_embed = discord.Embed(
                    title=f"🎶 Auflösung · Runde {round_number}/{rounds}",
                    description=(
                        f"Gesucht war {discord.utils.escape_markdown(correct[1])} von "
                        f"{discord.utils.escape_markdown(correct[2])}.\n\n"
                        + ("\n".join(round_lines) if round_lines else "Niemand hat eine Antwort abgegeben.")
                    ),
                    color=discord.Color.green(),
                )
                await message.edit(embed=result_embed, view=view)
                await asyncio.sleep(3)

            if not session["stopped"]:
                for user_id in session["participants"]:
                    db.execute(
                        """INSERT INTO musicquiz_scores(guild_id, user_id, points, correct_answers, games_played)
                           VALUES (?, ?, 0, 0, 1)
                           ON CONFLICT(guild_id, user_id) DO UPDATE SET games_played = games_played + 1""",
                        (guild_id, user_id),
                    )
                db.commit()
                final_rows = sorted(session["points"].items(), key=lambda item: item[1], reverse=True)[:10]
                summary = "\n".join(
                    f"#{index + 1} <@{user_id}> — {points} Punkte"
                    for index, (user_id, points) in enumerate(final_rows)
                ) or "Diesmal wurden keine Punkte erzielt."
                await ctx.send(embed=discord.Embed(
                    title="🏆 IronWolf · Music Quiz beendet",
                    description=f"Endergebnis:\n{summary}\n\nDauerhafte Rangliste: musicquiz leaderboard",
                    color=discord.Color.gold(),
                ), allowed_mentions=discord.AllowedMentions.none())
            else:
                await ctx.send("Das Music Quiz wurde vorzeitig beendet.", allowed_mentions=discord.AllowedMentions.none())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[MUSIC QUIZ] Game failed in guild {guild_id}: {type(exc).__name__}: {exc}", flush=True)
            await ctx.send("Das Music Quiz wurde wegen eines technischen Fehlers beendet. Bitte prüft die Railway-Laufzeitlogs.", allowed_mentions=discord.AllowedMentions.none())
        finally:
            if voice and voice.is_connected():
                try:
                    if voice.is_playing():
                        voice.stop()
                    await voice.disconnect(force=True)
                except (discord.ClientException, discord.HTTPException):
                    pass
            active_games.pop(guild_id, None)

    @start_game.error
    @add_song.error
    @remove_song.error
    @stop_game.error
    async def musicquiz_command_error(ctx, error):
        original = getattr(error, "original", error)
        if isinstance(original, commands.MissingPermissions):
            message = "Für diese Aktion brauchst du die Berechtigung Server verwalten."
        elif isinstance(original, commands.MissingRequiredArgument):
            message = "Bitte prüfe die Syntax mit musicquiz."
        elif isinstance(original, commands.BadArgument):
            message = "Bitte gib eine gültige Song-ID beziehungsweise Rundenzahl an."
        else:
            print(f"[MUSIC QUIZ] Command error: {type(original).__name__}: {original}", flush=True)
            message = "Der Music-Quiz-Befehl ist fehlgeschlagen. Bitte prüft die Railway-Logs."
        await ctx.reply(message, mention_author=False)

    print("[MUSIC QUIZ] Guess the Song module registered.", flush=True)
