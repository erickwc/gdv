"use strict";

// Aviso de actualizacion tipo Claude/VSCode: al abrir la app (empaquetada,
// nunca en "npm run dev" -- autoUpdater tira error sin un build real detras)
// se fija sola contra los Releases de GitHub del repo (ver "publish" en
// package.json).
//
// NO descarga ni instala sola (antes si, con autoDownload+quitAndInstall):
// probado en la app real, Gatekeeper rechaza el instalador con "Code
// signature ... did not pass validation" porque el build no esta firmado
// con un certificado de Apple Developer (pago, USD 99/ano). En vez de bajar
// ~170MB para terminar fallando en el ultimo paso, solo avisa que hay una
// version nueva -- el boton abre la pagina de Releases en el navegador (ver
// #update-toast-btn en app.js) y el usuario instala a mano, igual que la
// primera vez.
const { app } = require("electron");

function setupAutoUpdate(getMainWindow) {
  if (!app.isPackaged) return;

  // Import diferido: electron-updater toca cosas que solo tienen sentido en
  // un build empaquetado de verdad (busca app-update.yml adentro del
  // recurso), y fallar temprano en dev por esto no aporta nada.
  const { autoUpdater } = require("electron-updater");
  autoUpdater.autoDownload = false;

  const send = (channel, data) => {
    const win = getMainWindow();
    if (win && !win.isDestroyed()) win.webContents.send(channel, data);
  };

  autoUpdater.on("update-available", (info) => {
    send("update-status", { state: "available", version: info.version });
  });
  autoUpdater.on("update-not-available", () => {
    send("update-status", { state: "none" });
  });
  autoUpdater.on("error", (err) => {
    console.error("[autoUpdate]", err);
    send("update-status", { state: "error" });
  });

  autoUpdater.checkForUpdates().catch((err) => {
    console.error("[autoUpdate] fallo el chequeo inicial:", err);
  });
}

module.exports = { setupAutoUpdate };
