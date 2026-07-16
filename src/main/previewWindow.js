"use strict";

const path = require("path");
const { BrowserWindow } = require("electron");

// Ventana aparte para "Agrandar" el previsualizador -- reemplaza la 2a
// webview.create_window(..., hidden=True) de la version pywebview
// (webapp/main.py). Se crea oculta al arrancar y se muestra/oculta con
// fullscreen real de Windows, sin depender del tamano de la ventana
// principal (igual que Api.show_preview_window/hide_preview_window de la
// version vieja, pero el data URI ya lo tiene el renderer principal -- no
// hace falta pedirselo a Python de nuevo).
function createPreviewWindow() {
  const win = new BrowserWindow({
    width: 800,
    height: 600,
    show: false,
    backgroundColor: "#000000",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload-preview.js"),
      contextIsolation: true,
    },
  });
  win.loadFile(path.join(__dirname, "..", "renderer", "preview.html"));
  win.webContents.on("before-input-event", (_event, input) => {
    if (input.type === "keyDown" && input.key === "F12") {
      win.webContents.toggleDevTools();
    }
  });
  return win;
}

function showPreview(win, dataUri) {
  if (!win || !dataUri) return;
  win.webContents.send("set-preview-image", dataUri);
  win.show();
  win.setFullScreen(true);
}

// Igual que showPreview, pero para el loop reproduciendose (ver
// loop-preview-video en el renderer principal) -- src es el mismo
// file:///... que ya arma buildFileUrl() alla, se reusa tal cual.
function showPreviewVideo(win, src) {
  if (!win || !src) return;
  win.webContents.send("set-preview-video", src);
  win.show();
  win.setFullScreen(true);
}

function hidePreview(win) {
  if (!win) return;
  if (win.isFullScreen()) win.setFullScreen(false);
  win.hide();
  // Para que preview.html pause el <video> -- la ventana solo se oculta
  // (no se destruye), asi que sin esto seguia decodificando de fondo.
  win.webContents.send("preview-hidden");
}

module.exports = { createPreviewWindow, showPreview, showPreviewVideo, hidePreview };
