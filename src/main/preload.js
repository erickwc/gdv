"use strict";

const { contextBridge, ipcRenderer, webUtils } = require("electron");

// Superficie que ve el renderer principal. `call`/`onPyEvent` son el
// transporte crudo hacia el sidecar de Python; pywebview-shim.js (renderer)
// los envuelve para que app.js siga usando pywebview.api.xxx() sin cambios.
contextBridge.exposeInMainWorld("api", {
  call: (method, ...params) => ipcRenderer.invoke("py-call", method, params),
  onPyEvent: (callback) => {
    ipcRenderer.on("py-event", (_event, name, data) => callback(name, data));
  },
  // Ruta real de un archivo soltado (drag&drop) -- reemplaza el
  // pywebviewFullPath que pywebview inyectaba en cada File.
  getPathForFile: (file) => webUtils.getPathForFile(file),
  showPreviewWindow: (dataUri) => ipcRenderer.invoke("preview-show", dataUri),
  showPreviewVideo: (src) => ipcRenderer.invoke("preview-show-video", src),
  hidePreviewWindow: () => ipcRenderer.invoke("preview-hide"),
  // "win32"/"darwin"/"linux" -- el CSS lo usa para saber de que lado van
  // los botones nativos de la ventana (Windows: derecha via
  // titleBarOverlay; macOS: izquierda, los 3 semaforos via
  // trafficLightPosition) y dejarles el hueco correcto en .app-titlebar.
  platform: process.platform,
});
