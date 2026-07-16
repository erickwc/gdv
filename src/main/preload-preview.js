"use strict";

const { contextBridge, ipcRenderer } = require("electron");

// Superficie para la ventana de previsualizacion aparte (preview.html).
contextBridge.exposeInMainWorld("previewBridge", {
  hide: () => ipcRenderer.invoke("preview-hide"),
  onImage: (callback) => ipcRenderer.on("set-preview-image", (_event, src) => callback(src)),
  onVideo: (callback) => ipcRenderer.on("set-preview-video", (_event, src) => callback(src)),
  onHidden: (callback) => ipcRenderer.on("preview-hidden", () => callback()),
});
