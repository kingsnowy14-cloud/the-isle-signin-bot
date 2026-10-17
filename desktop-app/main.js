const { app, BrowserWindow, Menu, shell, dialog } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const DEFAULT_DASHBOARD_URL = process.env.IRONWOLF_DASHBOARD_URL || "https://king-snowy-at.base44.app/";
const ALLOWED_AUTH_HOSTS = new Set(["discord.com", "discordapp.com", "discordapp.net"]);
let mainWindow;
let dashboardUrl;

function readSettings() {
  const settingsPath = path.join(app.getPath("userData"), "settings.json");
  try {
    const parsed = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
    if (typeof parsed.dashboardUrl === "string" && isSafeHttpsUrl(parsed.dashboardUrl)) {
      dashboardUrl = parsed.dashboardUrl;
    }
    return parsed;
  } catch {
    return {};
  }
}

function writeSettings(settings) {
  const settingsPath = path.join(app.getPath("userData"), "settings.json");
  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
  fs.writeFileSync(settingsPath, JSON.stringify(settings, null, 2), { mode: 0o600 });
}

function isSafeHttpsUrl(value) {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

function isTrustedNavigation(value) {
  try {
    const target = new URL(value);
    const base = new URL(dashboardUrl);
    return target.origin === base.origin || ALLOWED_AUTH_HOSTS.has(target.hostname.toLowerCase());
  } catch {
    return false;
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 960,
    minHeight: 650,
    title: "IronWolf",
    autoHideMenuBar: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isTrustedNavigation(url)) {
      return { action: "allow" };
    }
    if (url.startsWith("https://")) shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isTrustedNavigation(url)) {
      event.preventDefault();
      if (url.startsWith("https://")) shell.openExternal(url);
    }
  });

  mainWindow.webContents.on("will-redirect", (event, url) => {
    if (!isTrustedNavigation(url)) {
      event.preventDefault();
      if (url.startsWith("https://")) shell.openExternal(url);
    }
  });

  mainWindow.loadURL(dashboardUrl).catch((error) => {
    dialog.showErrorBox(
      "IronWolf – Dashboard nicht erreichbar",
      "Das Dashboard konnte nicht geöffnet werden. Prüfe deine Internetverbindung und die Dashboard-Adresse.\n\n" + error.message
    );
  });

  mainWindow.on("closed", () => { mainWindow = null; });
}

function buildMenu(settings) {
  const template = [
    {
      label: "IronWolf",
      submenu: [
        {
          label: "Dashboard neu laden",
          accelerator: "Ctrl+R",
          click: () => mainWindow?.webContents.reload(),
        },
        {
          label: "Dashboard-Adresse ändern…",
          click: async () => {
            const result = await dialog.showMessageBox(mainWindow, {
              type: "info",
              title: "Dashboard-Adresse",
              message: "Ändere die Dashboard-Adresse in der Datei settings.json im IronWolf-Benutzerdatenordner.",
              detail: "Nur HTTPS-Adressen sind zulässig. Nach der Änderung IronWolf neu starten.",
              buttons: ["OK"],
            });
            return result;
          },
        },
        { type: "separator" },
        {
          label: "Beim Windows-Anmelden starten",
          type: "checkbox",
          checked: settings.autoStart !== false,
          click: (item) => {
            settings.autoStart = item.checked;
            writeSettings(settings);
            if (process.platform === "win32" && app.isPackaged) {
              app.setLoginItemSettings({ openAtLogin: item.checked, enabled: true });
            }
          },
        },
        { type: "separator" },
        { role: "quit", label: "Beenden" },
      ],
    },
    { role: "editMenu" },
    { role: "viewMenu" },
    { role: "windowMenu" },
    { role: "help", submenu: [{ label: "IronWolf Dashboard öffnen", click: () => shell.openExternal(dashboardUrl) }] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

app.whenReady().then(() => {
  const settings = readSettings();
  if (!dashboardUrl) dashboardUrl = DEFAULT_DASHBOARD_URL;
  if (!isSafeHttpsUrl(dashboardUrl)) {
    dialog.showErrorBox("IronWolf", "Die Dashboard-Adresse muss eine gültige HTTPS-Adresse sein.");
    app.quit();
    return;
  }

  buildMenu(settings);
  if (process.platform === "win32" && app.isPackaged) {
    app.setLoginItemSettings({ openAtLogin: settings.autoStart !== false, enabled: true });
  }
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
