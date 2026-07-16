"use strict";

const path = require("path");
const { app, BrowserWindow, ipcMain } = require("electron");

const { PythonBridge } = require("./pythonBridge");
const dialogs = require("./dialogs");
const previewWindow = require("./previewWindow");

let mainWindow = null;
let previewWin = null;
const bridge = new PythonBridge();

// Se probo el fondo Mica/Acrylic nativo de Windows 11 (backgroundMaterial +
// transparent:true) largo y tendido -- funcionaba, pero:
// - Solo existe en Windows (en Mac hubiera hecho falta "vibrancy", otra
//   API distinta, con sus propias limitaciones).
// - transparent:true le hacia perder a Windows el redondeo automatico de
//   esquinas, y ni CSS (border-radius/overflow) ni recortar el HWND a mano
//   (SetWindowRgn, ver windowShape.js) lograban un resultado limpio -- el
//   material Acrylic de DWM no seguia ninguna de las dos formas de recorte.
// Se decidio abandonarlo: ventana NORMAL (sin transparent, sin
// backgroundMaterial/vibrancy) con esquinas redondeadas nativas de verdad
// (Windows/macOS las dan solas en una ventana asi), y el efecto de
// profundidad lo da un gradiente fijo por CSS (ver --bg-gradient en
// styles.css) -- se ve parecido a translucido pero es 100% opaco, asi que
// funciona igual en Windows, Mac y Linux sin ninguna API nativa de por medio.
function createMainWindow() {
  const win = new BrowserWindow({
    width: 1180,
    height: 760,
    resizable: false,
    backgroundColor: "#0d0a14",
    titleBarStyle: "hidden",
    // Los botones nativos van a lados distintos segun la plataforma --
    // Windows/Linux: arriba a la derecha (titleBarOverlay). macOS: los 3
    // semaforos van arriba a la izquierda (trafficLightPosition); esa
    // plataforma no usa titleBarOverlay para esto.
    ...(process.platform === "darwin"
      ? { trafficLightPosition: { x: 16, y: 14 } }
      : {
          titleBarOverlay: {
            color: "#00000000",
            symbolColor: "#e6e6e6",
            height: 40,
          },
        }),
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile(path.join(__dirname, "..", "renderer", "index.html"));
  win.webContents.on("console-message", (event) => {
    console.log(`[renderer] ${event.message} (${event.sourceId}:${event.lineNumber})`);
  });
  // F12 para abrir DevTools -- con autoHideMenuBar:true el menu por defecto
  // de Electron (que trae F12 atado a "Toggle Developer Tools") no siempre
  // llega a instalarse/disparar, asi que se registra a mano para no
  // depender de eso.
  win.webContents.on("before-input-event", (_event, input) => {
    if (input.type === "keyDown" && input.key === "F12") {
      win.webContents.toggleDevTools();
    }
  });
  return win;
}

// Los 4 metodos que en la version pywebview abrian un dialogo nativo
// (self._window.create_file_dialog) ya no existen en api.py -- el dialogo
// lo abre Electron aca, y despues se llama al metodo "puro" de Python que
// ya existia (ingest_paths/set_template/register_texture_path) o al nuevo
// set_chosen_output(). Ver la tabla de la seccion "Que se mueve fuera de
// Python" del plan.
async function handlePyCall(event, method, params) {
  const win = BrowserWindow.fromWebContents(event.sender);

  if (method === "browse_media") {
    const paths = await dialogs.showOpenMediaDialog(win);
    if (!paths.length) return { ok: true, ignored: [], state: await bridge.call("get_state") };
    return bridge.call("ingest_paths", [paths]);
  }

  if (method === "browse_template") {
    const chosen = await dialogs.showOpenTemplateDialog(win);
    if (!chosen) return { ok: false, cancelled: true };
    return bridge.call("set_template", [chosen]);
  }

  if (method === "browse_texture_file") {
    const chosen = await dialogs.showOpenTextureDialog(win);
    if (!chosen) return null;
    return bridge.call("register_texture_path", [chosen]);
  }

  if (method === "choose_output_path") {
    const state = await bridge.call("get_state");
    const chosen = await dialogs.showSaveOutputDialog(win, state.output_path);
    if (!chosen) return state.output_path;
    return bridge.call("set_chosen_output", [chosen]);
  }

  return bridge.call(method, params);
}

function registerIpcHandlers() {
  ipcMain.handle("py-call", handlePyCall);

  ipcMain.handle("preview-show", (_event, dataUri) => {
    previewWindow.showPreview(previewWin, dataUri);
  });
  ipcMain.handle("preview-show-video", (_event, src) => {
    previewWindow.showPreviewVideo(previewWin, src);
  });
  ipcMain.handle("preview-hide", () => {
    previewWindow.hidePreview(previewWin);
  });
}

app.whenReady().then(async () => {
  await bridge.start();
  bridge.onEvent((name, data) => {
    if (name === "onReady") return; // consumido por pythonBridge.start()
    if (mainWindow) mainWindow.webContents.send("py-event", name, data);
  });

  registerIpcHandlers();

  mainWindow = createMainWindow();
  previewWin = previewWindow.createPreviewWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) mainWindow = createMainWindow();
  });
});

app.on("window-all-closed", () => {
  bridge.stop();
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  bridge.stop();
});
