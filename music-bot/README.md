# Eigenständiger Discord-Musikbot

Spotify wird ausschließlich für Suche, Titelinformationen und Spotify-Links verwendet. Die Wiedergabe erfolgt aus lokalen Audiodateien, für die du die erforderlichen Rechte hast. Der Bot lädt keine Audios von Spotify herunter und extrahiert keine Streams von Drittplattformen.

## Slash-Befehle
- `/play <song>` – sucht bei Spotify und spielt eine passende Datei aus der autorisierten lokalen Bibliothek.
- `/queue` – aktueller Titel und Warteschlange
- `/pause`, `/resume`, `/skip`, `/stop`

## Railway-Einrichtung
1. Erstelle einen **separaten Discord-Bot** im Discord Developer Portal und lade ihn mit den Scopes `bot` und `applications.commands` ein. Er benötigt View Channels, Connect und Speak.
2. Erstelle eine Spotify-App im [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) und kopiere Client ID und Client Secret.
3. Lege in Railway einen neuen Service aus diesem Repository an und setze Root Directory auf `/music-bot`.
4. Setze diese Umgebungsvariablen: `DISCORD_TOKEN`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`. Optional: `AUDIO_LIBRARY_DIR` (Standard `audio_library`) und `FFMPEG_PATH` (Standard `ffmpeg`).
5. Stelle sicher, dass FFmpeg installiert ist. Mit Nixpacks kann `NIXPACKS_PKGS=ffmpeg` gesetzt werden.
6. Stelle eine persistente Musikbibliothek bereit. Dateinamen sollten `Artist - Title.mp3` entsprechen. Nur Audiodateien verwenden, die du rechtmäßig abspielen darfst.

## Warum lokale Audiodateien?
Die Spotify Web API stellt Suchergebnisse und Metadaten bereit, aber keinen frei nutzbaren vollständigen Audiostream für einen Discord-Bot. Fehlt eine passende Datei, zeigt `/play` den Spotify-Treffer und erklärt, was fehlt. So bleibt die Audioquelle getrennt und autorisiert.
