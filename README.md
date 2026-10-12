# IronWolf — öffentliche Website für Netlify

Dark-gaming Landingpage, Discord-OAuth2-Anmeldung, Serverübersicht und automatisch aktualisierte öffentliche Modulliste. **Die Bot-Einladung und OAuth-Anmeldung sind vorbereitet; das Speichern von Bot-Einstellungen ist absichtlich noch nicht als funktionsfähig ausgegeben.** Dafür muss die vorhandene IronWolf-Bot-API mit serverseitig geprüften Berechtigungen verbunden werden.

## 1. Website veröffentlichen

1. Entpacke diese ZIP-Datei.
2. Lade den Ordner in ein GitHub-Repository hoch (alle Dateien im Projektstamm, insbesondere `netlify.toml`).
3. In Netlify: **Add new site → Import an existing project**, GitHub-Repository auswählen.
4. Build command leer lassen; Publish directory ist `public`. `netlify.toml` konfiguriert Publish-Verzeichnis und Functions.
5. Nach dem ersten Deploy eine Netlify-Domain erstellen, z. B. `https://dein-name.netlify.app`.

## 2. Discord Developer Portal

1. Öffne https://discord.com/developers/applications und wähle die Anwendung von IronWolf.
2. Unter **OAuth2 → General** kopiere die Client ID und den Client Secret.
3. Unter **OAuth2 → Redirects** exakt diese Adresse eintragen: `https://DEINE-NETLIFY-DOMAIN/auth/callback`.
4. Unter **Bot** den Bot öffentlich installierbar machen, wenn andere ihn einladen sollen. Veröffentliche niemals den Bot-Token.
5. Für die Einladung verwendet die Website `bot` und `applications.commands`. Standardmäßig wird kein pauschaler Administratorzugriff angefordert. Stelle die tatsächlich benötigten Bot-Rechte in `DISCORD_BOT_PERMISSIONS` ein (Discord-Berechtigungszahl); gib Administrator nur, wenn unbedingt nötig.

## 3. Netlify-Umgebungsvariablen

Netlify → Site configuration / Project configuration → **Environment variables**. Lege an:

- `DISCORD_CLIENT_ID` — Application Client ID
- `DISCORD_CLIENT_SECRET` — OAuth2 Client Secret (geheim)
- `DISCORD_REDIRECT_URI` — `https://DEINE-NETLIFY-DOMAIN/auth/callback` (muss exakt dem Redirect im Developer Portal entsprechen)
- `SESSION_SECRET` — zufällige geheime Zeichenfolge mit mindestens 32 Zeichen
- `BOT_API_URL` — öffentliche Basis-URL deiner Bot-API. Für deinen aktuellen Railway-Service: `https://ironwolf-bot.up.railway.app` (ohne abschließenden Slash)
- `DISCORD_BOT_PERMISSIONS` — optionale Discord-Berechtigungszahl, nur die wirklich nötigen Rechte

Nach dem Ändern von Umgebungsvariablen neu deployen. Geheimnisse nur in Netlify Variables bzw. Railway Variables speichern, nie in `public/`, GitHub oder JavaScript. Offizielle Netlify-Doku: https://docs.netlify.com/build/functions/environment-variables/

## 4. Bot-Einladung

Die Schaltfläche baut den Discord-Einladungslink aus `DISCORD_CLIENT_ID` und `DISCORD_BOT_PERMISSIONS`. Wenn kein Client ID gesetzt ist, bleibt nur ein Platzhalter-Link sichtbar. Nutze möglichst geringe Berechtigungen und überprüfe diese in der Discord-Developer-Konsole.

## 5. OAuth2 und Serverliste

Die Functions `auth-start`, `auth-callback`, `me`, `guilds` und `logout` führen OAuth2 aus. Verwendete Scopes: `identify guilds`. Die Sitzung wird als verschlüsseltes, HttpOnly-, Secure- und SameSite-Cookie gehalten. `SESSION_SECRET` ist zwingend. Die Serverliste filtert auf Nutzer mit `Administrator` oder `Server verwalten`.

Die Netlify-Domain muss HTTPS verwenden (Netlify stellt HTTPS für seine Domains bereit). Wenn du lokal testest, können `Secure`-Cookies auf `http://localhost` nicht funktionieren.

## 6. Neue Bot-Module automatisch anzeigen — auf deinen Bot angepasst

Die Website fragt `/api/modules` alle 60 Sekunden ab und zeigt Modulnamen, erkannte Befehlsanzahlen sowie aufklappbare Befehlslisten an. Die Netlify Function leitet diese Anfrage an `${BOT_API_URL}/api/modules` weiter. Im Paket liegt unter `bot-integration/dashboard.py` eine angepasste Version deines hochgeladenen `dashboard.py`. Sie fügt die schreibgeschützte Route `/api/modules` in den vorhandenen aiohttp-Webserver ein und listet aktive Cogs, Slash-Befehle und sichtbare Prefix-Befehle auf. Serverkonfiguration und Geheimnisse werden nicht ausgegeben.

1. **Sichere zuerst deine aktuelle `dashboard.py`** als Backup.
2. Ersetze im GitHub-Repository die bisherige `dashboard.py` durch `bot-integration/dashboard.py` aus diesem Paket (benenne die kopierte Datei im Repository wieder `dashboard.py`). `bot.py` muss nicht ersetzt werden.
3. In Railway muss `DASHBOARD_PASSWORD` gesetzt sein, damit dein vorhandenes Dashboard und damit diese Route gestartet werden. Deploye den Bot neu.
4. Öffne `https://DEINE-RAILWAY-DOMAIN/api/modules`. Erwartet wird JSON mit `name`, `updatedAt` und `modules`.
5. Setze in Netlify `BOT_API_URL` auf die Railway-Basis-URL ohne abschließenden Slash und deploye die Website erneut.

Neue registrierte Befehle/Module werden dann automatisch im Website-Manifest sichtbar, spätestens nach etwa 60 Sekunden. Die Route ist absichtlich öffentlich und read-only.

## 7. Dashboard-Schreibzugriffe — noch zu integrieren

Die Anmeldung und Serverübersicht sind vorbereitet. Das Dashboard ändert derzeit **keine** Moderations-, Log-, Anti-Raid-, Anti-Nuke- oder Ticket-Einstellungen. Vor Aktivierung müssen diese Schritte serverseitig implementiert werden:

- Netlify-Funktion sendet Anfrage mit Nutzer-/Server-ID an Bot-API.
- Bot prüft bei jeder Änderung Discord-Nutzer-ID, Servermitgliedschaft, Berechtigung und Bot-Zugriff auf den Server.
- Einstellungen werden pro Guild getrennt persistiert; Eingaben werden validiert und Änderungen protokolliert.
- API nutzt TLS, Rate-Limits, CSRF-Schutz/Origin-Prüfung und einen geheimen API-Schlüssel zwischen Netlify Function und Bot. Der API-Schlüssel darf nie an den Browser gesendet werden.
- Nie allein einer vom Browser übermittelten Guild-ID vertrauen.

## 8. Kostenloses Modell / Premium

Die Website zeigt kein kostenpflichtiges Premium-Angebot. Ein „Premium-System“ wäre in einem komplett kostenlosen Modell nur als Funktions-/Tier-Schalter ohne Zahlung sinnvoll. Entferne oder ändere Module in deinem Bot-Manifest, falls einzelne Funktionen nicht tatsächlich implementiert sind.

## Dateien

- `public/` — Website, CSS und JavaScript
- `netlify/functions/` — OAuth2, Serverliste, Konfiguration und Modul-Proxy
- `bot-integration/dashboard.py` — angepasste Version deines Dashboards mit automatischer Modulliste
- `bot-integration/module_endpoint.py` — optionales separates Beispiel für andere Bot-Webserver
- `netlify.toml` — Netlify Routing und Sicherheitsheader
