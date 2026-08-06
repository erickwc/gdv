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
  // Angosta la ventana al ancho del panel (o la devuelve a su ancho de
  // antes con null) cuando se cierra/abre el previsualizador -- el renderer
  // no puede cambiar el tamano de su propia ventana.
  collapsePreviewWindow: (width) => ipcRenderer.invoke("window-collapse-preview", width),
  // La ventana es resizable:false y en Windows eso tambien le prohibe la
  // pantalla completa -- hay que destrabarla justo antes de pedirla (ver
  // toggleExpand en app.js).
  setWindowResizable: (resizable) => ipcRenderer.invoke("window-set-resizable", resizable),
  // F11 -- lo atrapa el proceso principal antes de que el menu por defecto de
  // Electron lo use para la pantalla completa de la VENTANA, que se peleaba
  // con la del previsualizador (ver before-input-event en main.js).
  onExitPreviewFullscreen: (callback) => {
    ipcRenderer.on("exit-preview-fullscreen", () => callback());
  },
  // Avisa cuando la ventana termino de verdad de volver a su tamano de
  // antes al salir de pantalla completa (ver leave-html-full-screen en
  // main.js) -- entre el click/Esc y este momento hay un hueco donde la
  // ventana pasa por un tamano intermedio que no es el final; app.js oculta
  // el previsualizador en ese hueco y lo revela recien aca.
  onPreviewResizeSettled: (callback) => {
    ipcRenderer.on("preview-resize-settled", () => callback());
  },
  // Cualquier archivo de audio en renderer/sfx/, el mas nuevo por fecha de
  // modificacion -- ver sfx-export-done-candidate en main.js.
  getSfxExportDoneCandidate: () => ipcRenderer.invoke("sfx-export-done-candidate"),
  showPreviewWindow: (dataUri) => ipcRenderer.invoke("preview-show", dataUri),
  showPreviewVideo: (src) => ipcRenderer.invoke("preview-show-video", src),
  hidePreviewWindow: () => ipcRenderer.invoke("preview-hide"),
  // "win32"/"darwin"/"linux" -- el CSS lo usa para saber de que lado van
  // los botones nativos de la ventana (Windows: derecha via
  // titleBarOverlay; macOS: izquierda, los 3 semaforos via
  // trafficLightPosition) y dejarles el hueco correcto en .app-titlebar.
  platform: process.platform,
});
