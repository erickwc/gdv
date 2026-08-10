"use strict";

// Aviso de actualizacion tipo Claude/VSCode: al abrir la app (empaquetada,
// nunca en "npm run dev" -- autoUpdater tira error sin un build real detras)
// se fija sola contra los Releases de GitHub del repo (ver "publish" en
// package.json). Si hay una version mas nueva la descarga sola en segundo
// plano y, cuando ya esta lista, le avisa al renderer (ver onUpdateDownloaded
// en preload.js / la banderita en app.js) para que el usuario elija cuando
// reiniciar -- nunca se reinicia solo, eso cortaria una exportacion a medias.
const { app, ipcMain } = require("electron");

function setupAutoUpdate(getMainWindow) {
  if (!app.isPackaged) return;

  // Import diferido: electron-updater toca cosas que solo tienen sentido en
  // un build empaquetado de verdad (busca app-update.yml adentro del
  // recurso), y fallar temprano en dev por esto no aporta nada.
  const { autoUpdater } = require("electron-updater");
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;

  const send = (channel, data) => {
    const win = getMainWindow();
    if (win && !win.isDestroyed()) win.webContents.send(channel, data);
  };

  autoUpdater.on("update-available", (info) => {
    send("update-status", { state: "downloading", version: info.version });
  });
  autoUpdater.on("update-not-available", () => {
    send("update-status", { state: "none" });
  });
  autoUpdater.on("error", (err) => {
    console.error("[autoUpdate]", err);
    send("update-status", { state: "error" });
  });
  autoUpdater.on("update-downloaded", (info) => {
    send("update-status", { state: "ready", version: info.version });
  });

  ipcMain.handle("update-install-now", () => {
    autoUpdater.quitAndInstall();
  });

  autoUpdater.checkForUpdates().catch((err) => {
    console.error("[autoUpdate] fallo el chequeo inicial:", err);
  });
}

module.exports = { setupAutoUpdate };
