# IronWolf OpenAI-Assistent – Einrichtung

## Verwendung
- In Discord den Bot erwähnen und eine Frage dazuschreiben, z. B. `@IronWolf Wie kann ich ein Ticket erstellen?`.
- Es wird kein neuer globaler Slash-Command registriert.
- Gesprächskontext wird begrenzt im Arbeitsspeicher gehalten und ist nach einem Bot-Neustart weg.
- Die KI darf Serverinformationen nur lesen. Sie führt keine Moderationsaktionen aus.

## Railway-Variablen
Im Railway-Projekt den **IronWolf-Bot-Service** öffnen → **Variables** → diese Variablen hinzufügen:

- `OPENAI_API_KEY` = geheimer API-Schlüssel aus dem OpenAI API-Dashboard
- `OPENAI_MODEL` = optional; Standard ist `gpt-5.5`

Den API-Schlüssel niemals in GitHub, Discord oder Quellcode eintragen. Er wird nur als Railway-Variable gespeichert. Danach den Service redeployen bzw. neu starten, damit die Variable geladen wird.

OpenAI API-Abrechnung ist separat von einem ChatGPT-Abonnement. Websuche kann zusätzliche API-Kosten verursachen; prüfe im OpenAI API-Dashboard die Abrechnung und setze dort Limits/Benachrichtigungen, falls verfügbar.

## Berechtigungen und Datenschutz
- Discord muss den Bot erwähnte Nachrichten lesen können; die Message Content Intent ist im bestehenden Bot aktiviert. Kontrolliere auch den entsprechenden Intent im Discord Developer Portal.
- Antworten verwenden nur die Frage, den begrenzten Verlauf dieses Nutzers in diesem Kanal und eine knappe Serverübersicht (Servername, Mitgliederzahl, sichtbare Textkanäle und Rollennamen).
- Es werden keine anderen Chatnachrichten, privaten DMs oder nicht sichtbaren Textkanäle an die KI übergeben.
- Die Websuche erfolgt über das integrierte OpenAI-Tool. Antworten können Quellenlinks enthalten.
- Der Assistent ist beratend: Er vergibt keine Rollen, löscht keine Nachrichten, kickt/bant niemanden und verändert keine Servereinstellungen.

## Schnelltest
Nach dem Redeploy in einem Textkanal `@IronWolf Was kannst du mir hier helfen?` senden. In Railway Logs sollte `IronWolf OpenAI assistant loaded` erscheinen. Bei fehlendem Schlüssel antwortet IronWolf mit einem Hinweis, die Variable zu setzen.
