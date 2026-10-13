# IronWolf Premium – aktueller Implementierungsstand

## Status

Die erste Grundlage für kontobasierte Premium-Pläne ist vorhanden:

- `premium.py` liest manuell freigeschaltete Discord-Konto-IDs aus Railway-Variablen.
- `/premium` zeigt nach Discord-Anmeldung den Plan des angemeldeten Kontos.
- Premium umfasst in der aktuellen Plan-Definition Ticket-System Pro und Custom Branding.
- Premium+ umfasst Ticket-System Pro, Custom Branding, Security Pro und Automation Pro.
- Es ist **kein Zahlungsanbieter angebunden**. Das Dashboard nimmt keine Zahlungen entgegen und behauptet nicht, eine Zahlung geprüft zu haben.

Die Seite ist derzeit eine Status-/Planübersicht. Die jeweiligen Pro-Funktionen und die Durchsetzung der Entitlements in den Bot-Aktionen müssen in separaten Schritten integriert und getestet werden, bevor Premium verkauft werden sollte.

## Manuelle Testfreischaltung in Railway

Füge beim Railway-Service eine oder beide Variablen hinzu. Werte sind Discord-Benutzer-IDs, durch Kommas getrennt, nicht Benutzernamen:

- `PREMIUM_USER_IDS=123456789012345678`
- `PREMIUM_PLUS_USER_IDS=234567890123456789`

Premium+ hat Vorrang, falls dieselbe ID in beiden Variablen steht. Entferne eine ID, um die manuelle Freischaltung zu widerrufen. Die Variablen enthalten keine Zahlungsdaten und ersetzen keine spätere Zahlungsintegration.

Verwende ausschließlich echte Discord-Benutzer-IDs, die du selbst kontrollierst bzw. für Tests freischalten darfst. Keine Geheimnisse oder Bot-Tokens in diese Variablen eintragen.

## Noch vor einem Verkauf erforderlich

1. Zahlungsanbieter auswählen und Produkte/Preise dort konfigurieren.
2. Serverseitige Checkout-Erstellung sowie signaturgeprüfte Webhooks implementieren.
3. Entitlements dauerhaft und nachvollziehbar speichern; Verlängerungen, Kündigungen, Rückerstattungen und fehlgeschlagene Zahlungen verarbeiten.
4. Die vier Pro-Funktionen in Dashboard **und** Bot serverseitig gegen die Berechtigung prüfen.
5. Tests für unberechtigte Konten, abgelaufene Pläne, manipulierte Formulare und Webhook-Wiederholungen durchführen.

API-Schlüssel und Webhook-Secrets gehören ausschließlich in Railway Variables, niemals in Git.

## Aktueller Funktionsumfang der Premium-Stufe

- **Ticket-System Pro (Premium):** Ticket-Panel-Konfiguration ist nur mit einem berechtigten und verknüpften Premium-Konto speicherbar. Mitglieder, die ein Ticket öffnen, benötigen selbst kein Premium.
- **Custom Branding (Premium):** Ticket-Embeds können eine eigene sechsstellige Hex-Farbe und ein optionales HTTPS-Logo verwenden.
- **Security Pro (Premium+):** Die Slash-Befehle des `SecurityTools`-Cogs werden nur für einen Server mit aktiver Premium+-Berechtigung freigegeben. Die bereits vorhandenen allgemeinen Schutz-Listener bleiben davon getrennt.
- **Automation Pro (Premium+):** Über die Serververwaltung kann eine wiederkehrende Textnachricht konfiguriert werden. Das Intervall ist auf 60 Minuten bis 7 Tage begrenzt; Erwähnungen sind deaktiviert. Bei fehlgeschlagenem Versand wird mit 15 Minuten Abstand erneut versucht.

## Jahresmodell bei manueller Freischaltung

Da noch kein Zahlungsanbieter integriert ist, gibt es keine automatische Jahreslaufzeit, Rechnungsstellung oder Verlängerung. Für ein manuelles Jahresmodell musst du das Ablaufdatum extern dokumentieren und die Benutzer-ID nach Ablauf aus `PREMIUM_USER_IDS` bzw. `PREMIUM_PLUS_USER_IDS` entfernen. Nach Ablauf verliert das Konto beim nächsten Berechtigungscheck den Zugriff. Das Dashboard akzeptiert keine Zahlungsdaten.

## Aktivierungsschritte

1. Trage die Discord-Benutzer-ID in die passende Railway-Variable ein.
2. Melde dich mit genau diesem Discord-Konto im Dashboard an.
3. Öffne einen Server, dann **Premium verwalten**, und verknüpfe das Konto mit diesem Server.
4. Premium erlaubt Ticket-System Pro und Custom Branding. Premium+ erlaubt zusätzlich Security Pro und Automation Pro.
5. Für Automation Pro: Öffne **Automation Pro**, wähle den Zielkanal, Nachricht und Intervall, und aktiviere die Regel.

Die Pro-Module sind an das aktuell verknüpfte Konto gebunden. Ändere `premium_owner_id` nicht direkt, wenn du nicht sicher bist, wer die Serverbindung kontrolliert. Die App bietet absichtlich keinen automatischen Besitzerwechsel an.
