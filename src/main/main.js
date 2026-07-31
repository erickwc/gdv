"use strict";

const path = require("path");
const { app, BrowserWindow, ipcMain, screen } = require("electron");

const { PythonBridge } = require("./pythonBridge");
const dialogs = require("./dialogs");
const previewWindow = require("./previewWindow");

let mainWindow = null;
let previewWin = null;
const bridge = new PythonBridge();

// Suavizado de texto en GRIS, no ClearType. En Windows, Chromium dibuja el
// texto con subpixeles (ClearType) y correccion de gamma: sobre fondo oscuro
// eso ENGORDA los trazos finos y les deja un fleco de color, asi que la
// tipografia Light de 300 termina pareciendo un 400 sucio. Figma rasteriza en
// gris, y de ahi viene casi toda la diferencia de "se ve mas elegante en el
// diseno". El styles.css ya lo pedia con -webkit-font-smoothing:antialiased,
// pero esa propiedad SOLO existe en macOS -- en Windows no hace nada, y este
// switch es la unica forma de conseguirlo. Tiene que ir antes de whenReady().
app.commandLine.appendSwitch("disable-lcd-text");

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
  // Las dos teclas que hay que manejar a mano por culpa del menu por defecto
  // de Electron, que con autoHideMenuBar:true sigue instalado aunque no se
  // vea: F12 porque su atajo no siempre llega a dispararse, y F11 porque el
  // suyo hace justo lo que NO se quiere (ver abajo).
  //
  // F12 abre DevTools -- el menu lo trae atado a "Toggle Developer Tools",
  // pero no siempre llega a instalarse/disparar, asi que se registra aca
  // para no depender de eso.
  win.webContents.on("before-input-event", (event, input) => {
    if (input.type !== "keyDown") return;
    if (input.key === "F12") {
      win.webContents.toggleDevTools();
      return;
    }
    // F11 tiene que morir ACA. Con autoHideMenuBar:true el menu por defecto
    // de Electron sigue instalado (solo esta escondido) y trae F11 atado a
    // "Toggle Full Screen", que es la pantalla completa DE LA VENTANA -- otra
    // cosa que la del previsualizador, que es la del documento
    // (requestFullscreen sobre #preview-surface, ver toggleExpand en app.js).
    // Mezclarlas rompia la app: estando agrandado el previsualizador, F11
    // apagaba la de la ventana pero dejaba el documento en pantalla completa,
    // asi que la ventana volvia a 1180x760 con el previsualizador todavia
    // "maximizado" adentro y sin forma de salir.
    //
    // preventDefault mata el atajo del menu (y de paso el keydown en la
    // pagina, que es todo lo que hace falta). En su lugar se le pide al
    // renderer que salga de la pantalla completa del previsualizador, que es
    // lo que el usuario espera de F11 estando agrandado. Salir no necesita
    // gesto del usuario, asi que funciona igual viniendo por IPC -- ENTRAR si
    // lo necesitaria, por eso F11 no agranda: para eso esta el boton.
    if (input.key === "F11") {
      event.preventDefault();
      win.webContents.send("exit-preview-fullscreen");
    }
  });
  // Vuelve al tamano fijo en cuanto la ventana sale de la pantalla completa
  // del previsualizador -- el renderer solo se encarga de destrabarla antes
  // de entrar (ver "window-set-resizable" mas abajo y toggleExpand en
  // app.js). Cubre las tres formas de salir: el boton, Esc, y cualquier
  // atajo del sistema.
  win.on("leave-html-full-screen", () => win.setResizable(false));

  // Red de seguridad contra el DESINCRONIZADO entre las dos pantallas
  // completas: la de la VENTANA y la del DOCUMENTO (#preview-surface) son
  // independientes, y apagar la de la ventana por su cuenta deja al documento
  // creyendo que sigue agrandado. Eso es exactamente lo que se veia: la
  // ventana volvia a su tamano normal pero el previsualizador seguia
  // "maximizado" adentro, tapando la app, con el boton de achicar cayendo
  // encima de la X del sistema y sin forma de salir.
  //
  // Atrapar F11 (arriba) evita la via conocida, pero no es suficiente: la
  // pantalla completa de la ventana la puede apagar cualquier cosa (otro
  // atajo, el sistema, doble clic en la barra). Por eso, cada vez que la
  // VENTANA sale de pantalla completa, se le avisa al renderer -- si el
  // documento quedo agrandado, sale; si ya estaba normal, no hace nada. Asi
  // los dos estados no pueden quedar separados, venga de donde venga.
  win.on("leave-full-screen", () => win.webContents.send("exit-preview-fullscreen"));
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

  // Imagen propia de portada: el dialogo lo abre Electron y la ruta se queda
  // en el renderer (Python la recibe recien al guardar, en save_cover) -- por
  // eso no delega en ningun metodo de Python.
  if (method === "browse_cover_image") {
    const chosen = await dialogs.showOpenCoverImageDialog(win);
    return { ok: !!chosen, path: chosen || null };
  }

  if (method === "choose_output_path") {
    const state = await bridge.call("get_state");
    const chosen = await dialogs.showSaveOutputDialog(win, state.output_path);
    if (!chosen) return state.output_path;
    return bridge.call("set_chosen_output", [chosen]);
  }

  return bridge.call(method, params);
}

// Ancho de contenido de antes de cerrar el previsualizador, para poder
// volver exactamente a ese (no a un numero hardcodeado que se desincronice
// del tamano con el que arranca la ventana).
const widthBeforeCollapse = new WeakMap();

// setContentSize deja fija la esquina superior izquierda, asi que al cerrar el
// previsualizador la ventana se encogia hacia la derecha y quedaba corrida en
// la pantalla (habia que reacomodarla a mano cada vez). Se recentra despues de
// cada cambio de ancho, en los dos sentidos.
//
// No se usa win.center() a proposito: con varios monitores manda la ventana al
// principal, aunque este trabajando en otro. Esto la centra en el monitor donde
// ya esta, y sobre workArea (el escritorio util) para no meterse debajo de la
// barra de tareas.
function centerOnItsDisplay(win) {
  const area = screen.getDisplayMatching(win.getBounds()).workArea;
  const [w, h] = win.getSize();
  win.setPosition(
    Math.round(area.x + (area.width - w) / 2),
    Math.round(area.y + (area.height - h) / 2),
  );
}

// Deslizamiento al abrir/cerrar el previsualizador. Corto a proposito: esto
// redimensiona la VENTANA (lo dibuja el sistema en cada paso, no es una
// transicion de CSS), y estirarlo se siente pesado en vez de suave.
const COLLAPSE_MS = 260;
const collapseAnims = new WeakMap();

function animateContentWidth(win, to) {
  const previa = collapseAnims.get(win);
  if (previa) clearInterval(previa.timer);
  // Si ya venia una animacion a medio camino, la ventana YA esta destrabada:
  // hay que conservar el estado original (isResizable() ahora diria true
  // siempre y la ventana quedaria redimensionable para siempre).
  const destrabar = previa ? previa.destrabar : !win.isResizable();
  if (destrabar) win.setResizable(true);

  const [desde, alto] = win.getContentSize();
  const t0 = Date.now();
  return new Promise((resolve) => {
    const timer = setInterval(() => {
      if (win.isDestroyed()) {
        clearInterval(timer);
        collapseAnims.delete(win);
        resolve(false);
        return;
      }
      const t = Math.min(1, (Date.now() - t0) / COLLAPSE_MS);
      // easeInOutCubic: arranca y termina despacio. Con easeOutCubic (salida
      // a maxima velocidad) el primer paso se comia 273 de los 815 px de un
      // saque -- medido -- y justo ese tiron era lo que se veia de golpe.
      // Windows solo alcanza a dibujar ~12 pasos de ventana en este tiempo,
      // asi que lo que importa es que queden REPARTIDOS, no que haya muchos.
      const k = t < 0.5
        ? 4 * t * t * t
        : 1 - Math.pow(-2 * t + 2, 3) / 2;
      win.setContentSize(Math.round(desde + (to - desde) * k), alto);
      centerOnItsDisplay(win);
      if (t >= 1) {
        clearInterval(timer);
        collapseAnims.delete(win);
        if (destrabar) win.setResizable(false);
        resolve(true);
      }
    }, 16);
    collapseAnims.set(win, { timer, destrabar });
  });
}

function registerIpcHandlers() {
  ipcMain.handle("py-call", handlePyCall);

  // width = ancho del panel para cerrar el previsualizador; null para volver
  // a abrirlo. Ver togglePreviewPanel en app.js.
  ipcMain.handle("window-collapse-preview", (event, width) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win) return false;
    const [current] = win.getContentSize();
    const target = width ? Math.round(width) : (widthBeforeCollapse.get(win) || current);
    // Solo se anota el ancho a recuperar si NO hay una animacion a medio
    // camino: si no, un doble click rapido guardaba un ancho intermedio y al
    // reabrir la ventana volvia a un tamano cualquiera.
    if (width && !collapseAnims.has(win)) widthBeforeCollapse.set(win, current);
    // La ventana es resizable:false (ver createMainWindow) -- en Windows eso
    // le hace ignorar setContentSize, asi que animateContentWidth la habilita
    // mientras dura el deslizamiento y la vuelve a trabar al terminar.
    return animateContentWidth(win, target);
  });

  // Windows no deja poner en pantalla completa una ventana con
  // resizable:false: la pagina entraba en fullscreen pero la ventana seguia
  // de 1180x760 y el video quedaba en una esquina de la pantalla. El
  // renderer destraba esto antes de pedir la pantalla completa y lo vuelve a
  // trabar al salir (ver toggleExpand en app.js).
  // Solo destraba: volver a trabarla es cosa del evento
  // "leave-html-full-screen" (ver createMainWindow), que dispara cuando la
  // ventana YA salio -- hacerlo desde el renderer, en el fullscreenchange,
  // es una carrera con el propio Electron y la dejaba a medio camino.
  ipcMain.handle("window-set-resizable", (event, resizable) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win) return false;
    win.setResizable(!!resizable);
    return true;
  });

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
