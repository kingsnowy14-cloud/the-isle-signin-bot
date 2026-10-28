# IronWolf Control Center – Einrichtung

Das Control Center wird zusammen mit dem Discord-Bot gestartet. Es verwendet dieselbe Railway-App und liest/schreibt die vorhandene serverbezogene `config.json`. Die bestehenden Ticket- und persistenten Button-IDs werden von diesem Dashboard nicht verändert.

## 1. Railway-Variablen

Öffne dein IronWolf-Projekt in Railway → Service → Variables und setze:

- `DASHBOARD_ENABLED=true`
- `DASHBOARD_PUBLIC_URL=https://DEINE-APP-DOMAIN`
- `DASHBOARD_REDIRECT_URI=https://DEINE-APP-DOMAIN/callback`
- `DASHBOARD_SECRET_KEY=EIN-LANGER-ZUFAELLIGER-GEHEIMER-WERT`
- `DISCORD_CLIENT_ID=DEINE-APPLICATION-ID`
- `DISCORD_CLIENT_SECRET=DEIN-OAUTH2-CLIENT-SECRET`

Verwende bei `DASHBOARD_PUBLIC_URL` und `DASHBOARD_REDIRECT_URI` die tatsächliche öffentliche HTTPS-Domain deiner Railway-App. Erfinde keine Domain; kopiere sie aus Railway.

Für einen starken geheimen Schlüssel kannst du lokal beispielsweise mit Python erzeugen:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Den Schlüssel und das Client-Secret niemals in GitHub committen oder öffentlich teilen.

## 2. Discord OAuth2 konfigurieren

1. Öffne das [Discord Developer Portal](https://discord.com/developers/applications) und wähle dieselbe Discord-Application wie dein Bot.
2. Öffne **OAuth2** und trage unter **Redirects** exakt den Wert von `DASHBOARD_REDIRECT_URI` ein.
3. Kopiere die Application ID in `DISCORD_CLIENT_ID`.
4. Erzeuge bzw. kopiere das OAuth2 Client Secret in `DISCORD_CLIENT_SECRET`.
5. Speichere die Einstellungen und starte/deploye den Railway-Service neu.

Das Dashboard fordert nur die OAuth2-Bereiche `identify` und `guilds` an. Es fragt nicht nach dem Bot-Token. Es zeigt nur Server an, auf denen der eingeloggte Discord-Nutzer **Server verwalten** oder **Administrator** besitzt und der Bot bereits Mitglied ist.

## 3. Funktionen

- **Neue Server starten deaktiviert:** Wenn IronWolf einem neuen Discord-Server hinzugefügt wird, führt er dort keine Serveraktionen, Befehle oder Hintergrundfunktionen aus. Öffne im Control Center den Bereich **Allgemeine Einstellungen** und aktiviere **„IronWolf auf diesem Server aktivieren“**, um den Bot einzuschalten.
- **Bestehende Server bleiben unverändert:** Bei Servern, die bereits in der Konfiguration stehen, wird der bisherige Zustand beibehalten. Fehlende alte Konfigurationswerte gelten weiterhin wie bisher als aktiv.
- Server-Einstellungen per Formular ändern (z. B. Willkommensnachricht, ausgewählte Kanäle/Rollen und vorhandene Schutzschalter).
- Cog-Module pro Server aktivieren/deaktivieren.
- Prefix- und Slash-Befehle pro Server aktivieren/deaktivieren.
- Eine neue `discord.py`-Cog-Vorlage erstellen und als Python-Datei herunterladen.
- In Discord: `/module-manager` zeigt den Dashboard-Link; `/module-status` zeigt den Modulstatus.

## 4. Neue Module

Der Modulgenerator erstellt eine **Vorlage**, die du vor der Verwendung prüfen und anpassen solltest. Sie wird absichtlich nicht automatisch ausgeführt. Um eine Extension produktiv hinzuzufügen, muss sie reviewed, in das GitHub-Projekt übernommen und sicher geladen werden. Führe niemals Python-Code aus, nur weil er aus einem Formular stammt.

## Hinweise

- Das Dashboard verwendet Discord OAuth2 und prüft serverbezogene Verwaltungsrechte auf jeder Server-Seite.
- `DASHBOARD_SECRET_KEY` muss dauerhaft gleich bleiben, sonst werden bestehende Browser-Sitzungen ungültig.
- Änderungen an Einstellungen werden in die vorhandene `config.json` geschrieben. Sichere die Railway-Daten/Volume vor größeren Umbauten.
- Das Dashboard startet im selben Service auf dem von Railway bereitgestellten `PORT`.
- Nach dem Deploy müssen die Slash-Commands synchronisiert sein. Falls `/module-manager` und `/module-status` nicht erscheinen, prüfe Railway-Logs und die `GUILD_ID`-Variable.
