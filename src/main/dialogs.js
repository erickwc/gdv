"use strict";

const { dialog } = require("electron");

// Mismas extensiones que engine.py (IMAGE_EXTS/VIDEO_EXTS/AUDIO_EXTS), sin el
// punto -- asi las usa el formato de filtros de Electron.
const IMAGE_EXTS = ["jpg", "jpeg", "png", "bmp", "webp", "tif", "tiff"];
const VIDEO_EXTS = ["mp4", "mov", "mkv", "webm", "avi", "m4v", "gif"];
const AUDIO_EXTS = ["mp3", "wav", "flac", "aac", "m4a", "ogg", "wma", "opus"];

async function showOpenMediaDialog(win) {
  const result = await dialog.showOpenDialog(win, {
    properties: ["openFile", "multiSelections"],
    filters: [
      { name: "Imagen, video o audio", extensions: [...IMAGE_EXTS, ...VIDEO_EXTS, ...AUDIO_EXTS] },
      { name: "Todos los archivos", extensions: ["*"] },
    ],
  });
  return result.canceled ? [] : result.filePaths;
}

async function showOpenTemplateDialog(win) {
  const result = await dialog.showOpenDialog(win, {
    properties: ["openFile"],
    filters: [{ name: "Plantilla PNG", extensions: ["png"] }],
  });
  return result.canceled ? null : result.filePaths[0];
}

async function showOpenTextureDialog(win) {
  const result = await dialog.showOpenDialog(win, {
    properties: ["openFile"],
    filters: [{ name: "Imagen", extensions: IMAGE_EXTS }],
  });
  return result.canceled ? null : result.filePaths[0];
}

async function showSaveOutputDialog(win, defaultPath) {
  const result = await dialog.showSaveDialog(win, {
    defaultPath: defaultPath || "video.mp4",
    filters: [{ name: "Video MP4", extensions: ["mp4"] }],
  });
  return result.canceled ? null : result.filePath;
}

module.exports = {
  showOpenMediaDialog,
  showOpenTemplateDialog,
  showOpenTextureDialog,
  showSaveOutputDialog,
};
