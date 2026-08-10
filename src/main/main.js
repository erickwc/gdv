"use strict";

const path = require("path");
const fs = require("fs");
const os = require("os");
const { app, BrowserWindow, ipcMain, screen, session, shell } = require("electron");

const { PythonBridge } = require("./pythonBridge");
const dialogs = require("./dialogs");
const previewWindow = require("./previewWindow");

let mainWindow = null;
let previewWin = null;
const bridge = new PythonBridge();

// Tamano inicial de la ventana Y piso para el resize manual (arrastrar el
// borde, el boton verde nativo de Mac) -- ver createMainWindow. Con la
// ventana resizable:true de nuevo (antes arrancaba trabada) no habia ningun
// limite: se podia achicar hasta romper el layout. El colapso del panel de
// controles (window-collapse-preview, mas abajo) SI necesita ir mas angosto
// que esto a proposito (deja solo el panel, 365px) -- ese caso afloja el
// minimo antes de encoger y lo repone despues, este de aca es solo para
// cuando el usuario arrastra el borde a mano.
const MIN_WIDTH = 1180;
const MIN_HEIGHT = 760;

// Suavizado de texto en GRIS, no ClearType. En Windows, Chromium dibuja el
// texto con subpixeles (ClearType) y correccion de gamma: sobre fondo oscuro
// eso ENGORDA los trazos finos y les deja un fleco de color, asi que la
// tipografia Light de 300 termina pareciendo un 400 sucio. Figma rasteriza en
// gris, y de ahi viene casi toda la diferencia de "se ve mas elegante en el
// diseno". El styles.css ya lo pedia con -webkit-font-smoothing:antialiased,
// pero esa propiedad SOLO existe en macOS -- en Windows no hace nada, y este
// switch es la unica forma de conseguirlo. Tiene que ir antes de whenReady().
app.commandLine.appendSwitch("disable-lcd-text");

// Apenas hay un <video>/<audio> en la pagina (el previsualizador del loop,
// el beat), Chromium se registra ante el sistema como reproductor de medios
// -- en Mac eso le hace agarrar los botones de pausa/retroceder/adelantar
// del teclado (y el widget "Reproduciendo ahora"), aunque esos botones no
// hagan nada aca: pedido explicito, molestaba para controlar Spotify con
// la app abierta de fondo. Tiene que ir antes de whenReady(), como el
// switch de arriba.
app.commandLine.appendSwitch("disable-features", "HardwareMediaKeyHandling,MediaSessionService");

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
    width: MIN_WIDTH,
    height: MIN_HEIGHT,
    minWidth: MIN_WIDTH,
    minHeight: MIN_HEIGHT,
    // resizable:true (el default) a proposito: antes arrancaba en false y
    // solo se destrababa como efecto secundario de agrandar el
    // previsualizador (ver toggleExpand en app.js) -- hasta hacer eso, ni
    // arrastrar el borde ni el boton verde nativo de Mac (maximizar)
    // funcionaban. animateContentWidth (mas abajo, la animacion de
    // colapsar el panel) ya se adapta solo a esto: si la ventana ya viene
    // resizable, no la destraba ni la vuelve a trabar sola.
    resizable: true,
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
  // Vuelve al tamano/posicion de antes en cuanto la ventana sale de la
  // pantalla completa del previsualizador (ver toggleExpand en app.js).
  // Cubre las tres formas de salir: el boton, Esc, y cualquier atajo del
  // sistema. Ya NO vuelve a trabar resizable:false -- la ventana es
  // redimensionable siempre (ver createMainWindow), asi que no hay nada
  // que destrabar antes de entrar en pantalla completa ni que re-trabar
  // al salir.
  //
  // "leave-html-full-screen" dispara ANTES de que termine la animacion de
  // salida de pantalla completa nativa de Mac -- win.isFullScreen() todavia
  // da true en ese instante, y setBounds() llamado ahi se IGNORA en
  // silencio (asi se probo: el tamano guardado nunca se aplicaba, la
  // ventana quedaba como el propio SO la dejara caer, a veces maximizada).
  // Se espera a que isFullScreen() de verdad pase a false (sondeando cada
  // 30ms, con un limite de 1s por las dudas) antes de restaurar el tamano.
  // Mientras se espera (arriba): la animacion nativa de Mac por si sola deja
  // la ventana en lo que el SO decida (a veces maximizada) ANTES de que
  // esperar() consiga aplicar setBounds() -- el usuario veia eso como un
  // "corte" en la previsualizacion (un tamano de golpe, despues otro) en vez
  // de una sola transicion prolija. Se le avisa al renderer para que oculte
  // el contenido del previsualizador apenas empieza a salir (fullscreenchange
  // ya lo hace solo, ver app.js) y "preview-resize-settled" cuando esto
  // termina de verdad, para que lo vuelva a mostrar recien con el tamano
  // final. El hueco entre esos dos momentos deja de verse -- sigue
  // ocupando lo mismo, pero ahora en negro en vez de con la imagen mal
  // encajada a mitad de camino.
  win.on("leave-html-full-screen", () => {
    const bounds = boundsBeforeFullscreen.get(win);
    const intentos = 33; // ~1s a 30ms cada uno
    let i = 0;
    const esperar = () => {
      if (win.isDestroyed()) return;
      if (win.isFullScreen() && i < intentos) {
        i += 1;
        setTimeout(esperar, 30);
        return;
      }
      if (bounds) win.setBounds(bounds);
      if (!win.isDestroyed()) win.webContents.send("preview-resize-settled");
    };
    esperar();
  });

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

// ------------------------------------------ Instagram: fotos sin recortar
//
// El og:image de un post de Instagram viene con un recorte cuadrado FIRMADO
// en la propia URL de la CDN (el parametro oh/oe es una firma que cubre
// TODA la query string -- probado a mano contra una URL real: tocar
// cualquier parte, hasta el propio recorte, devuelve "URL signature
// mismatch"). No hay forma de pedir otra version de esa imagen por ese
// camino -- por eso Python (_try_download_page_image, api.py) siempre
// entregaba el cuadrado recortado para las fotos de Instagram.
//
// La foto SIN recortar si esta en el DOM ya renderizado: Instagram le pone
// un cartel de "Iniciar sesion" ENCIMA del contenido a quien no tiene
// sesion, pero no lo bloquea -- probado contra un post real, el contenido
// de verdad (fotos, comentarios) carga igual, con sesion o sin ella. Por
// eso esta funcion abre una ventana de Electron OCULTA (no headless de
// verdad -- es el mismo Chromium de la app, asi que carga y ejecuta JS
// como cualquier pestana), espera a que Instagram termine de pintar, y lee
// las fotos directo del DOM (ver instagram-extract.js para el detalle de
// COMO se identifican, que no es tan simple como "buscar <article>").
//
// Solo para POSTS DE FOTO (url con /p/, filtradas en app.js): un reel o un
// post con video ya se descarga bien por el camino de siempre (yt-dlp, ver
// _download_video en api.py) y no hace falta tocarlo -- por eso esta
// funcion devuelve {ok:false} apenas encuentra un <video> en el articulo,
// para que el llamador (startDownload en app.js) caiga de vuelta a
// download_from_link sin haber gastado nada mas que el intento.
//
// El script que se inyecta en la ventana oculta vive en su PROPIO archivo
// (instagram-extract.js), leido como texto -- no como template literal
// adentro de esta funcion: un regex con \d en un template literal se rompe
// en silencio (Node se come la barra al parsear el string, antes de que
// executeJavaScript lo vea), asi que hacia falta separarlo del todo.
const instagramExtractScript = fs.readFileSync(
  path.join(__dirname, "instagram-extract.js"), "utf-8"
);

async function resolveInstagramPhotos(url) {
  // Sesion PROPIA y efimera (no la default de la app) -- dos motivos:
  //   1. asi el bloqueo de red de abajo (accounts.meta.com) queda scopeado a
  //      esta ventana, no afecta a mainWindow ni a nada mas de la app.
  //   2. de paso, no acumula cookies de Instagram entre una descarga y la
  //      siguiente -- cada resolucion arranca de cero.
  const ses = session.fromPartition(`instagram-scrape-${Date.now()}`, { cache: false });
  // El dialogo NATIVO de Windows Hello/llave de acceso (probado DOS veces:
  // le seguia apareciendo al usuario en medio de una descarga incluso con
  // el bloqueo de red de abajo puesto). Iba a dos frentes que no alcanzaban
  // por separado:
  //   - navigator.credentials por JS (instagram-block-webauthn.js): con
  //     contextIsolation, Chromium le da a cada "mundo" (preload vs pagina)
  //     su PROPIO wrapper de los objetos del DOM -- sobreescribir la
  //     propiedad desde el preload no se refleja del lado de la pagina.
  //   - bloquear accounts.meta.com por red: navigator.credentials.get() es
  //     una llamada del navegador AL SISTEMA OPERATIVO, no necesariamente
  //     pasa por un pedido de red a ese dominio antes de mostrar el dialogo
  //     -- "accounts.meta.com" que se ve en el cartel de Windows es el
  //     "relying party id" que va DENTRO del pedido WebAuthn, no una URL
  //     que haya que visitar primero.
  // Lo que si funciona: Permissions-Policy, una politica que Chromium hace
  // cumplir a nivel de MOTOR (no de JS de la pagina, no de un mundo
  // aislado) -- publickey-credentials-get/-create en "()" (lista vacia de
  // origenes permitidos) apaga la API entera para el documento, pase lo
  // que pase en su JS. Se inyecta como si el propio servidor la hubiera
  // mandado, en la respuesta de CUALQUIER pedido (instagram.com incluido,
  // que es de donde sale el pedido de verdad).
  ses.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        "Permissions-Policy": ["publickey-credentials-get=(), publickey-credentials-create=()"],
      },
    });
  });
  // El bloqueo de red se deja igual (defensa extra, no hace nada malo):
  // esta ventana nunca necesita iniciar sesion contra accounts.meta.com.
  ses.webRequest.onBeforeRequest(
    { urls: ["*://accounts.meta.com/*", "*://*.accounts.meta.com/*"] },
    (details, callback) => callback({ cancel: true })
  );

  const win = new BrowserWindow({
    show: false,
    // sandbox:true ademas de contextIsolation/nodeIntegration:false -- esta
    // ventana carga una pagina de VERDAD de un sitio de terceros (a
    // diferencia de mainWindow, que solo carga el index.html propio), asi
    // que el aislamiento tiene que ser el maximo: nada de lo que corra ahi
    // (el JS de Instagram) puede tocar Node ni el resto de la app.
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      session: ses,
      // Defensa extra, aunque el bloqueo de red de arriba es el que de
      // verdad importa (ver el comentario grande ahi).
      preload: path.join(__dirname, "instagram-block-webauthn.js"),
    },
  });
  // El user-agent de la app lleva el nombre del programa metido adentro
  // ("generador-de-video-electron/0.1.0 ... Electron/43.1.0") -- nada
  // parecido a un navegador de verdad, y esta ventana SI necesita parecerlo
  // (carga una pagina de un sitio ajeno, no la propia). Con el user-agent
  // de Electron sin mas, la pagina volvia sin la foto (probado: 8s de
  // sondeo sin encontrar <article> nunca, contra ~1-2s con un Chrome
  // comun) -- un Chrome de escritorio corriente resuelve esto.
  win.webContents.setUserAgent(
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
  );
  try {
    // Entrar directo al post (un link "en frio", sin ninguna cookie/sesion
    // asentada antes) devolvia el cartel de login SIN el contenido de
    // atras -- pasar primero por la portada, dejar que ponga sus cookies, y
    // recien ahi ir al post imita mejor una visita real.
    await win.loadURL("https://www.instagram.com/");
    await new Promise((resolve) => setTimeout(resolve, 1200));
    if (win.isDestroyed()) return { ok: false };
    await win.loadURL(url);
    if (win.isDestroyed()) return { ok: false };
    // Instagram es una SPA pesada: el HTML inicial no trae las fotos, las
    // carga por su cuenta despues -- el contenido tarda un tiempo VARIABLE
    // en aparecer (probado: 2.5s fijos a veces alcanzaban, a veces no,
    // mismo post). El sondeo (cada 400ms hasta 8s) esta DENTRO del script
    // inyectado, no de este lado -- ver instagram-extract.js.
    return await win.webContents.executeJavaScript(instagramExtractScript);
  } catch (e) {
    return { ok: false };
  } finally {
    if (!win.isDestroyed()) win.destroy();
  }
}

// La URL resuelta arriba SI se puede descargar directo (no lleva firma
// invalidable por nada que hagamos -- es la imagen real, no un recorte
// generado al vuelo). fetch nativo: Electron 43 trae Node lo bastante
// nuevo como para no necesitar el modulo https a mano.
async function downloadInstagramPhoto(imageUrl) {
  const res = await fetch(imageUrl);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const buffer = Buffer.from(await res.arrayBuffer());
  const destino = path.join(os.tmpdir(), `genvideo_ig_${Date.now()}.jpg`);
  fs.writeFileSync(destino, buffer);
  return destino;
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

  // Los dos de Instagram: ver resolveInstagramPhotos/downloadInstagramPhoto
  // mas arriba. Ninguno pasa por Python en el camino de "encontrar la foto"
  // -- recien download_instagram_photo, que YA tiene el archivo listo en
  // disco, llama a ingest_paths (el mismo metodo que usa browse_media unas
  // lineas mas arriba) para sumarlo al estado de siempre.
  if (method === "resolve_instagram_photos") {
    return resolveInstagramPhotos(params[0]);
  }

  if (method === "download_instagram_photo") {
    let destino;
    try {
      destino = await downloadInstagramPhoto(params[0]);
    } catch (e) {
      return { ok: false, error: "No se pudo descargar la foto." };
    }
    return bridge.call("ingest_paths", [[destino]]);
  }

  return bridge.call(method, params);
}

// Ancho de contenido de antes de cerrar el previsualizador, para poder
// volver exactamente a ese (no a un numero hardcodeado que se desincronice
// del tamano con el que arranca la ventana).
const widthBeforeCollapse = new WeakMap();

// Tamano/posicion de la ventana justo antes de entrar en pantalla completa
// del previsualizador -- ver window-set-resizable y "leave-html-full-screen"
// mas abajo. Sin esto, salir de la pantalla completa en Mac podia dejar la
// ventana MAXIMIZADA en vez de devolverla al tamano chico que el usuario
// tenia antes: requestFullscreen() sobre el documento pone a la VENTANA
// entera en fullscreen nativo por debajo, y macOS no siempre reconstruye
// solo el tamano de antes al salir (mas todavia si resizable cambia justo en
// medio de la animacion de salida).
const boundsBeforeFullscreen = new WeakMap();

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
    // minWidth (ver createMainWindow) evita que el usuario achique la
    // ventana de mas arrastrando el borde, pero TAMBIEN bloquearia esta
    // misma animacion: colapsar deja la ventana en solo el ancho del panel
    // (365px), bien por debajo del minimo. Se afloja antes de encoger y se
    // repone despues de volver a agrandar -- nunca queda mas angosta que
    // MIN_WIDTH salvo mientras esta genuinamente colapsada.
    if (width) win.setMinimumSize(0, MIN_HEIGHT);
    return animateContentWidth(win, target).then((ok) => {
      if (!width) win.setMinimumSize(MIN_WIDTH, MIN_HEIGHT);
      return ok;
    });
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
    // Justo antes de destrabar para entrar en pantalla completa: se guarda
    // el tamano/posicion de AHORA (el que el usuario tenia) para que
    // "leave-html-full-screen" pueda devolverlo tal cual al salir.
    if (resizable) boundsBeforeFullscreen.set(win, win.getBounds());
    win.setResizable(!!resizable);
    return true;
  });

  // Redes sociales en el modal de creditos (ver .credits-social-btn en
  // index.html/app.js): solo http/https, para no dejar que un dato raro
  // termine abriendo un esquema tipo file:// o algo ejecutable.
  ipcMain.handle("open-external", (_event, url) => {
    let parsed;
    try { parsed = new URL(url); } catch (e) { return false; }
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
    shell.openExternal(url);
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

  // Sonido de "exportacion lista" (ver playExportDoneSound en app.js):
  // cualquier archivo de audio que haya en renderer/sfx/, SIN pedirle un
  // nombre exacto -- pedido explicito, para poder ir probando sonidos
  // nuevos con solo arrastrarlos a la carpeta, sin renombrar nada. La
  // primera version pedia un nombre fijo ("export-done.<extension>") y el
  // usuario probo con un archivo con otro nombre (faaah.mp3): no sonaba
  // nada, sin avisar por que.
  //
  // El renderer no puede leer el directorio por su cuenta: fetch/XHR sobre
  // file:// no lista carpetas de forma confiable en Chromium, asi que el
  // listado lo hace el proceso principal, que si tiene fs entero.
  //
  // El MAS NUEVO por fecha de modificacion, no el primero alfabetico: asi
  // el usuario puede dejar variantes viejas en la carpeta sin borrarlas y
  // el ultimo archivo que solto es siempre el que se prueba.
  const SFX_DIR = path.join(__dirname, "..", "renderer", "sfx");
  const SFX_EXTS = new Set([".wav", ".mp3", ".ogg", ".m4a", ".flac", ".aac", ".opus"]);
  ipcMain.handle("sfx-export-done-candidate", () => {
    let nombres;
    try {
      nombres = fs.readdirSync(SFX_DIR);
    } catch (e) {
      return { ok: false };
    }
    const candidatos = nombres
      .filter((n) => SFX_EXTS.has(path.extname(n).toLowerCase()))
      .map((n) => {
        let mtime = 0;
        try { mtime = fs.statSync(path.join(SFX_DIR, n)).mtimeMs; } catch (e) { /* se descarta abajo si no hay stat */ }
        return { nombre: n, mtime };
      })
      .sort((a, b) => b.mtime - a.mtime);
    return candidatos.length ? { ok: true, filename: candidatos[0].nombre } : { ok: false };
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
