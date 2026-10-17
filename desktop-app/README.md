# IronWolf Desktop für Windows

Die Desktop-App öffnet das IronWolf-Webdashboard in einem eigenständigen Windows-Fenster. Nach der Installation kann sie standardmäßig beim Windows-Anmelden starten. Die App öffnet dann das Dashboard; falls die Website eine Anmeldung verlangt, muss diese weiterhin durchgeführt werden.

## Installer erstellen

Im GitHub-Repository unter **Actions → Build IronWolf Windows Desktop** den Workflow manuell starten. Nach erfolgreichem Lauf das Artefakt **IronWolf-Windows-Installer** herunterladen. Darin liegt der NSIS-Installer für Windows x64.

Der Workflow kann auch automatisch bei Änderungen an `desktop-app/**` oder am Workflow selbst laufen.

## Dashboard-Adresse

Standardmäßig wird `https://king-snowy-at.base44.app/` geöffnet. Für eine andere Adresse kann in `%APPDATA%/ironwolf-desktop/settings.json` ein `dashboardUrl`-Wert mit einer HTTPS-Adresse eingetragen werden, zum Beispiel:

```json
{
  "dashboardUrl": "https://example.com/"
}
```

Nur HTTPS-Adressen sind erlaubt. Die Standardadresse ist eine Konfiguration und kein Ersatz für die eigentliche IronWolf-Discord-OAuth-Anmeldung.

## Autostart

Die App aktiviert unter Windows den Autostart für den angemeldeten Benutzer. Im Menü **IronWolf → Beim Windows-Anmelden starten** kann er deaktiviert oder wieder aktiviert werden. Windows kann Autostart-Einträge außerdem in den Systemeinstellungen verwalten.

## Sicherheit und Grenzen

- Die App enthält keine Discord-Bot-Tokens oder OAuth-Client-Geheimnisse.
- Discord-Anmeldung und Berechtigungsprüfungen bleiben Aufgabe des serverseitigen Webdashboards; die Desktop-App gewährt selbst keine Rechte.
- Dashboard- und Discord-Anmeldeseiten laufen in einer isolierten Electron-Ansicht ohne Node-Integration. Externe HTTPS-Links werden im Standardbrowser geöffnet.
- Ein erfolgreicher GitHub-Actions-Build erzeugt den Installer als Artefakt. Er veröffentlicht ihn nicht automatisch als GitHub Release und bestätigt auch keinen Railway-Deploy.
