// Panel de controles + previsualizador en vivo. Galerias de miniaturas
// para Plantilla/Texturas, previsualizador con fotograma real de ffmpeg
// (Preview module + Api.request_preview), titulo editable = nombre de
// salida. El link de YouTube/Pinterest (yt-dlp) sigue en Contenido del
// video.

function $(sel) {
  return document.querySelector(sel);
}
function $$(sel) {
  return Array.from(document.querySelectorAll(sel));
}

let lastState = null;
let templatesCache = { templates: [], active: null };
let availableTextures = [];
let selectedTexturePath = null; // cual tarjeta de textura muestra sus controles debajo

// -------------------------------------------------------------- iconos
//
// Nada de emojis -- lineas SVG estilo Tabler (mismo formato que la
// familia de iconos que va a mandar el usuario: viewBox 24x24,
// stroke=currentColor, sin relleno). trash/edit/download/music son
// exactamente los suyos; check/close/reset/expand/chevronDown/plus se
// dibujaron a mano en el mismo estilo mientras llega el resto de su set
// -- un solo lugar (ICONS) para reemplazarlos despues.
const ICONS = {
  trash: '<path d="M4 7l16 0" /><path d="M10 11l0 6" /><path d="M14 11l0 6" />'
    + '<path d="M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12" />'
    + '<path d="M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3" />',
  edit: '<path d="M4 20h4l10.5 -10.5a2.828 2.828 0 1 0 -4 -4l-10.5 10.5v4" />'
    + '<path d="M13.5 6.5l4 4" />',
  download: '<path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2 -2v-2" />'
    + '<path d="M7 11l5 5l5 -5" /><path d="M12 4l0 12" />',
  // Los 3 de la tarjeta de resultado, tal como los mando el usuario:
  // folder-open (que ya estaba igual, mas abajo), video y photo-edit. El
  // photo-edit venia con stroke-width 2 en su copia; va en 1 como los demas
  // para que los tres se vean del mismo grosor.
  video: '<path d="M15 10l4.553 -2.276a1 1 0 0 1 1.447 .894v6.764a1 1 0 0 1 -1.447 .894l-4.553 -2.276v-4" />'
    + '<path d="M3 8a2 2 0 0 1 2 -2h8a2 2 0 0 1 2 2v8a2 2 0 0 1 -2 2h-8a2 2 0 0 1 -2 -2l0 -8" />',
  photoEdit: '<path d="M15 8h.01" />'
    + '<path d="M11 20h-4a3 3 0 0 1 -3 -3v-10a3 3 0 0 1 3 -3h10a3 3 0 0 1 3 3v4" />'
    + '<path d="M4 15l4 -4c.928 -.893 2.072 -.893 3 0l3 3" />'
    + '<path d="M14 14l1 -1c.31 -.298 .644 -.497 .987 -.596" />'
    + '<path d="M18.42 15.61a2.1 2.1 0 0 1 2.97 2.97l-3.39 3.42h-3v-3l3.42 -3.39" />',
  music: '<path d="M3 17a3 3 0 1 0 6 0a3 3 0 0 0 -6 0" /><path d="M13 17a3 3 0 1 0 6 0a3 3 0 0 0 -6 0" />'
    + '<path d="M9 17v-13h10v13" /><path d="M9 8h10" />',
  // Los dos del boton de agrandar, tal como los mando el usuario (sin el
  // <path> del recuadro transparente que traen al principio, que no dibuja
  // nada -- ninguno de los de aca lo lleva): arrows-maximize para el estado
  // normal y arrows-minimize para cuando ya esta en pantalla completa. Son
  // pareja del mismo set -- las mismas 4 esquinas, unas hacia afuera y las
  // otras hacia adentro -- asi entrar y salir se ve como el mismo icono dado
  // vuelta. Antes eran las 4 esquinas redondeadas.
  expand: '<path d="M16 4l4 0l0 4" /><path d="M14 10l6 -6" />'
    + '<path d="M8 20l-4 0l0 -4" /><path d="M4 20l6 -6" />'
    + '<path d="M16 20l4 0l0 -4" /><path d="M14 14l6 6" />'
    + '<path d="M8 4l-4 0l0 4" /><path d="M4 4l6 6" />',
  shrink: '<path d="M5 9l4 0l0 -4" /><path d="M3 3l6 6" />'
    + '<path d="M5 15l4 0l0 4" /><path d="M3 21l6 -6" />'
    + '<path d="M19 9l-4 0l0 -4" /><path d="M15 9l6 -6" />'
    + '<path d="M19 15l-4 0l0 4" /><path d="M15 15l6 6" />',
  // Forma del recorte, en la esquina del mini preview de "Ajustar imagen"
  // (resize de Tabler, lo mando el usuario -- sin el <path> del recuadro
  // transparente que trae al principio, que no dibuja nada).
  resize: '<path d="M4 11v8a1 1 0 0 0 1 1h8m-9 -14v-1a1 1 0 0 1 1 -1h1m5 0h2m5 0h1a1 1 0 0 1 1 1v1m0 5v2m0 5v1a1 1 0 0 1 -1 1h-1" />'
    + '<path d="M4 12h7a1 1 0 0 1 1 1v7" />',
  chevronDown: '<path d="M6 9l6 6l6 -6" />',
  chevronsLeft: '<path d="M11 7l-5 5l5 5" /><path d="M17 7l-5 5l5 5" />',
  chevronsRight: '<path d="M7 7l5 5l-5 5" /><path d="M13 7l5 5l-5 5" />',
  // Boton que abre/cierra el previsualizador (layout-sidebar de Tabler, sin
  // el <path> del recuadro transparente que trae al principio: no dibuja nada
  // y ninguno de los de aca lo lleva). Dibuja franja angosta a la izquierda y
  // area grande a la derecha, que es la maqueta real de la app: panel de
  // controles + previsualizador.
  //
  // Antes iba el par -collapse/-expand, con chevron adentro y uno por estado.
  // Se cambio por peso visual: este boton comparte franja con los controles
  // NATIVOS de Windows (minimizar y cerrar), que son trazos rectos sueltos --
  // al lado de esos, caja redondeada + divisoria + chevron era con diferencia
  // lo mas denso de la franja y se leia como pegoteado. Sin chevron el estado
  // lo dan el title y la propia ventana moviendose.
  //
  // Va a 20px con trazo 1.2, no al 16/1 del resto (ver las llamadas a
  // iconSvg): a 16px un trazo de 1 sobre viewBox de 24 mide 0.67 px reales,
  // nunca cae sobre un pixel y sale una mancha gris. Medido rasterizando las
  // dos: a 20/1.2 los lados y la divisoria salen como lineas limpias. No
  // rompe la consistencia con los demas iconos porque este es el unico que
  // vive en la franja de arriba, sin ninguno al lado con que compararlo.
  sidebarToggle: '<path d="M4 6a2 2 0 0 1 2 -2h12a2 2 0 0 1 2 2v12a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2l0 -12" />'
    + '<path d="M9 4l0 16" />',
  plus: '<path d="M12 5l0 14" /><path d="M5 12l14 0" />',
  percentage: '<path d="M16 17a1 1 0 1 0 2 0a1 1 0 1 0 -2 0" /><path d="M6 7a1 1 0 1 0 2 0a1 1 0 1 0 -2 0" />'
    + '<path d="M6 18l12 -12" />',
  photoScan: '<path d="M15 8h.01" /><path d="M6 13l2.644 -2.644a1.21 1.21 0 0 1 1.712 0l3.644 3.644" />'
    + '<path d="M13 13l1.644 -1.644a1.21 1.21 0 0 1 1.712 0l1.644 1.644" />'
    + '<path d="M3 7v-2a2 2 0 0 1 2 -2h2" /><path d="M3 17v2a2 2 0 0 0 2 2h2" />'
    + '<path d="M17 3h2a2 2 0 0 1 2 2v2" /><path d="M17 21h2a2 2 0 0 0 2 -2v-2" />',
  folderOpen: '<path d="M5 19l2.757 -7.351a1 1 0 0 1 .936 -.649h12.307a1 1 0 0 1 .986 1.164l-.996 5.211a2 2 0 0 1 -1.964 1.625h-14.026a2 2 0 0 1 -2 -2v-11a2 2 0 0 1 2 -2h4l3 3h7a2 2 0 0 1 2 2v2" />',
  deviceFloppy: '<path d="M6 4h10l4 4v10a2 2 0 0 1 -2 2h-12a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2" />'
    + '<path d="M12 4l0 4l6 0l0 -4" /><path d="M9 17a2 2 0 1 0 4 0a2 2 0 0 0 -4 0" />',
  cloudDownload: '<path d="M19 18a3.5 3.5 0 0 0 0 -7h-1a5 4.5 0 0 0 -11 -2a4.6 4.4 0 0 0 -2.1 8.4" />'
    + '<path d="M12 13l0 9" /><path d="M9 19l3 3l3 -3" />',
  // Los tres estados del volumen del beat en el previsualizador (volume,
  // volume-2 y volume-3 de Tabler): dos ondas, una sola cuando esta bajo, y la
  // cruz cuando esta en silencio. El parlante es EL MISMO trazo en los tres,
  // asi que cambiar de estado no mueve nada del icono, solo lo que tiene al
  // lado. Ver syncBeatVolumeUi.
  volume: '<path d="M15 8a5 5 0 0 1 0 8" /><path d="M17.7 5a9 9 0 0 1 0 14" />'
    + '<path d="M6 15h-2a1 1 0 0 1 -1 -1v-4a1 1 0 0 1 1 -1h2l3.5 -4.5a.8 .8 0 0 1 1.5 .5v14a.8 .8 0 0 1 -1.5 .5l-3.5 -4.5" />',
  volumeLow: '<path d="M15 8a5 5 0 0 1 0 8" />'
    + '<path d="M6 15h-2a1 1 0 0 1 -1 -1v-4a1 1 0 0 1 1 -1h2l3.5 -4.5a.8 .8 0 0 1 1.5 .5v14a.8 .8 0 0 1 -1.5 .5l-3.5 -4.5" />',
  volumeOff: '<path d="M6 15h-2a1 1 0 0 1 -1 -1v-4a1 1 0 0 1 1 -1h2l3.5 -4.5a.8 .8 0 0 1 1.5 .5v14a.8 .8 0 0 1 -1.5 .5l-3.5 -4.5" />'
    + '<path d="M16 10l4 4m0 -4l-4 4" />',
  // rotate-clockwise de Tabler, tal cual (verificado contra el original, no
  // inventado a mano -- ver rotate-media-btn en index.html).
  rotate: '<path d="M4.05 11a8 8 0 1 1 .5 4m-.5 5v-5h5" />',
};

function iconSvg(name, size = 16, strokeWidth = 1) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" `
    + `stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
}

// Iconos "filled" de Tabler (fill=currentColor, sin stroke) -- juego
// aparte de ICONS/iconSvg porque esos son estilo "outline" (sin relleno).
// (fileDescription/fileMusic vivian aca para los chips de "Contenido del
// video"; esos chips ya no llevan icono -- ver index.html.)
const ICONS_FILLED = {
  playerPlay: '<path d="M6 4v16a1 1 0 0 0 1.524 .852l13 -8a1 1 0 0 0 0 -1.704l-13 -8a1 1 0 0 0 -1.524 .852z" />',
  playerPause: '<path d="M9 4h-2a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h2a2 2 0 0 0 2 -2v-12a2 2 0 0 0 -2 -2z" />'
    + '<path d="M17 4h-2a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h2a2 2 0 0 0 2 -2v-12a2 2 0 0 0 -2 -2z" />',
  check: '<path d="M20.707 6.293a1 1 0 0 1 0 1.414l-10 10a1 1 0 0 1 -1.414 0l-5 -5a1 1 0 0 1 1.414 -1.414l4.293 4.293l9.293 -9.293a1 1 0 0 1 1.414 0" />',
  close: '<path d="M6.707 5.293l5.293 5.292l5.293 -5.292a1 1 0 0 1 1.414 1.414l-5.292 5.293l5.292 5.293a1 1 0 0 1 -1.414 1.414l-5.293 -5.292l-5.293 5.292a1 1 0 1 1 -1.414 -1.414l5.292 -5.293l-5.292 -5.293a1 1 0 0 1 1.414 -1.414" />',
};

function iconSvgFilled(name, size = 16) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="currentColor">${ICONS_FILLED[name] || ""}</svg>`;
}

// Icono de "Guardar preset" (rediseno) -- viewBox 16x16 propio, no encaja
// en el molde 24x24/stroke=currentColor de ICONS/iconSvg, asi que va
// literal en vez de sumarse ahi.
const SAVE_ICON_SVG = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">'
  + '<path d="M9.33268 2.66663V5.33329H5.33268V2.66663M3.99935 2.66663H10.666L13.3327 5.33329V12C13.3327 12.3536 13.1922 12.6927 12.9422 12.9428C12.6921 13.1928 12.353 13.3333 11.9993 13.3333H3.99935C3.64573 13.3333 3.30659 13.1928 3.05654 12.9428C2.80649 12.6927 2.66602 12.3536 2.66602 12V3.99996C2.66602 3.64634 2.80649 3.3072 3.05654 3.05715C3.30659 2.8071 3.64573 2.66663 3.99935 2.66663ZM6.66602 9.33329C6.66602 9.68691 6.80649 10.0261 7.05654 10.2761C7.30659 10.5262 7.64573 10.6666 7.99935 10.6666C8.35297 10.6666 8.69211 10.5262 8.94216 10.2761C9.19221 10.0261 9.33268 9.68691 9.33268 9.33329C9.33268 8.97967 9.19221 8.64053 8.94216 8.39048C8.69211 8.14044 8.35297 7.99996 7.99935 7.99996C7.64573 7.99996 7.30659 8.14044 7.05654 8.39048C6.80649 8.64053 6.66602 8.97967 6.66602 9.33329Z" stroke="white" stroke-linecap="round" stroke-linejoin="round"/>'
  + '</svg>';

// Icono de "Añadir" de Plantillas/Texturas (mismo caso que SAVE_ICON_SVG:
// viewBox propio de 23x23, no entra en el molde de ICONS/iconSvg) -- va con
// stroke=currentColor para que el color lo ponga el CSS (.btn-add lo fija en
// blanco, ver styles.css). Sin el clipPath del archivo original: era el
// recuadro rotado que exporta Figma, no recortaba nada visible.
const ADD_ICON_SVG = '<svg width="23" height="23" viewBox="0 0 23 23" fill="none" xmlns="http://www.w3.org/2000/svg">'
  + '<path d="M16.9713 11.3137H5.6576M11.3145 5.65686V16.9706" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"/>'
  + '</svg>';

// Botones/elementos estaticos del HTML que se quedan sin icono en el
// marcado (para no repetir el SVG en dos lugares) -- se rellenan una vez
// al arrancar.
function fillStaticIcons() {
  $("#remove-media-btn").innerHTML = iconSvg("trash");
  $("#rotate-media-btn").innerHTML = iconSvg("rotate");
  $("#audio-chip").querySelector(".btn-icon").innerHTML = iconSvg("trash");
  $("#preview-expand").innerHTML = iconSvg("expand");
  $("#crop-mode-trigger").innerHTML = iconSvg("resize");
  $("#loop-preview-play").innerHTML = iconSvgFilled("playerPlay");
  $("#loop-preview-toggle").innerHTML = iconSvgFilled("playerPause");
  // "Guardar preset": tenia (por error) el mismo icono de carpeta que
  // antes usaba el boton de ruta de salida (ya quitado, ver conversacion
  // del rediseno -- #output-label solo se abre clickeando el texto ahora);
  // un preset se GUARDA, asi que le toca un icono de guardar (disquete,
  // icono propio del rediseno -- ver SAVE_ICON_SVG).
  $$(".preset-dropdown-chevron").forEach((el) => (el.innerHTML = iconSvg("chevronDown")));
  $("#preset-save").innerHTML = SAVE_ICON_SVG;
  $("#preset-save-confirm").innerHTML = iconSvgFilled("check");
  $("#preset-save-cancel").innerHTML = iconSvgFilled("close");
  $("#preset-save-choice-cancel").innerHTML = iconSvgFilled("close");
  $("#preview-toggle-btn").innerHTML = iconSvg("sidebarToggle", 20, 1.2);
  $("#template-add-btn").innerHTML = ADD_ICON_SVG;
  $("#texture-add-btn").innerHTML = ADD_ICON_SVG;
  // Tarjeta de resultado: check a la izquierda y las 3 acciones a la derecha.
  $(".result-card-check").innerHTML = iconSvgFilled("check");
  $("#open-folder-btn").innerHTML = iconSvg("folderOpen");
  $("#open-video-btn").innerHTML = iconSvg("video");
  $("#save-cover-btn").innerHTML = iconSvg("photoEdit");
  $("#cover-modal-close").innerHTML = iconSvgFilled("close");
  $$(".unit-percent").forEach((el) => (el.innerHTML = iconSvg("percentage", 12)));
  // El atajo de pegar YA funciona con Cmd+V en Mac (es el evento nativo
  // "paste" del navegador, no una tecla fija atada a mano -- ver el
  // listener mas abajo), pero el texto se habia quedado escrito para
  // Windows. data-platform lo pone index.html bien temprano, antes de que
  // corra este script.
  if (document.documentElement.dataset.platform === "darwin") {
    $(".dropzone-sub").textContent = "o haz clic para buscarlos, o pega una imagen con ⌘V";
  }
}

function formatDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// Inverso de formatDuration -- acepta "m:ss" (lo que ya se muestra) o
// segundos sueltos ("125"), para los campos manuales de Recorte del loop.
// null si el texto no se puede interpretar (el llamador decide el fallback).
function parseDuration(text) {
  const trimmed = String(text).trim();
  if (!trimmed) return null;
  const parts = trimmed.split(":");
  if (parts.length === 2) {
    const m = Number(parts[0]), s = Number(parts[1]);
    if (!Number.isFinite(m) || !Number.isFinite(s)) return null;
    return m * 60 + s;
  }
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

// --------------------------------------------------------------- render

function render(state) {
  // Si TENIA beat antes de este estado -- antes de pisar lastState, para
  // comparar contra lo de recien. Booleano (hay/no hay) y no la ruta exacta
  // a proposito: audio_preview_path pasa de la ruta original a la copia en
  // aac en cuanto termina de armarse en segundo plano (ver
  // _maybe_start_audio_preview_proxy en api.py), y esa SEGUNDA vuelta no es
  // "se agrego un beat nuevo" -- comparando rutas el loop se reiniciaba de
  // nuevo solo, unos segundos despues de arrancar, sin que el usuario
  // tocara nada.
  const teniaBeat = !!(lastState && (lastState.audio_preview_path || lastState.audio_path));
  lastState = state;
  // Se cargo otro medio: el resultado del export anterior deja de mostrarse
  // (cover_available es justo esa senal, ver api.py).
  if (!state.cover_available) setExportResult(null);
  renderChips(state);
  FocusPicker.applyState(state);
  renderTemplateGallery(state);
  renderTextureGallery(state);
  LoopSlider.applyState(state);
  renderSpeedControl(state);
  renderScale(state);
  renderPresets(state);
  renderOutput(state);
  Preview.applyState(state);
  Preview.schedulePreview();
  // Cualquier refresh() (plantilla, texturas, etc.) puede haber cambiado
  // como se ve el video -- sin esto el loop generado quedaba con la
  // version vieja hasta el proximo cambio de recorte/velocidad (los unicos
  // que ya disparaban esto). request_loop_preview() se ignora solo si no
  // hay video cargado, asi que no hace falta filtrar aca; lo que si filtra
  // es syncLoopTrimDefined, que decide si ya hay un pedazo que componer.
  syncLoopTrimDefined(state);
  invalidateLoopPreview();
  // Se acaba de agregar un beat (no habia ninguno) con el loop YA sonando
  // sin sonido -- nada mira audio_path mientras corre (startBeat solo se
  // llama al arrancar/pausar), asi que sin esto quedaba mudo hasta la
  // proxima pausa. Se reinicia el loop entero (no solo el beat) para que
  // arranquen sincronizados desde el principio, en vez de que el beat entre
  // a mitad de una vuelta ya empezada. Solo pasa si YA estaba sonando --
  // pedido explicito, cargar el beat con todo pausado no dispara nada.
  const hayBeatAhora = !!(state.audio_preview_path || state.audio_path);
  if (!teniaBeat && hayBeatAhora && LivePreview.isPlaying()) {
    LivePreview.seek(0);
    startBeat(true);
  }
}

function refresh() {
  return pywebview.api.get_state().then(render);
}

function refreshTemplates() {
  return pywebview.api.list_templates().then((data) => {
    templatesCache = data;
    if (lastState) renderTemplateGallery(lastState);
  });
}

function refreshAvailableTextures() {
  return pywebview.api.list_available_textures().then((list) => {
    availableTextures = list;
    if (lastState) renderTextureGallery(lastState);
  });
}

// CSS text-transform:capitalize no sirve para nombres que ya vienen en
// MAYUSCULAS (titulos de YouTube, archivos exportados asi) -- capitalize
// solo sube de caso la primera letra de cada palabra, no baja el resto.
// Esto si baja todo a minuscula y despues sube la primera letra de cada
// palabra (separadas por espacio, asi que "archivo.mp4" queda como
// "Archivo.mp4" y no como "Archivo.Mp4").
function titleCase(text) {
  return text
    .toLowerCase()
    .replace(/(^|\s)\S/g, (c) => c.toUpperCase());
}

function renderChips(state) {
  $("#content-section").hidden = !state.media_path && !state.audio_path;
  // (La tarjeta de resultado, que es donde vive el boton de la portada, la
  // manejan setExportResult/onJobDone -- ver la seccion de generacion.)

  const mediaChip = $("#media-chip");
  if (state.media_path) {
    mediaChip.hidden = false;
    mediaChip.querySelector(".chip-name").textContent = titleCase(state.media_filename);
    mediaChip.querySelector(".chip-kind").textContent = state.media_kind_text || "";
    // media_was_vertical (tamano ORIGINAL), no media_size (el actual):
    // un video que llega girado 180/270 -- no solo 90 -- necesita mas de
    // un click para quedar derecho, y a mitad de camino el archivo esta
    // horizontal un rato. Si se chequeara el tamano actual el boton se
    // escondia despues del primer click y no habia forma de seguir
    // girando hasta la orientacion correcta.
    $("#rotate-media-btn").hidden = !(state.media_is_video && state.media_was_vertical);
  } else {
    mediaChip.hidden = true;
  }

  const audioChip = $("#audio-chip");
  if (state.audio_path) {
    audioChip.hidden = false;
    audioChip.querySelector(".chip-name").textContent = titleCase(state.audio_filename);
    const kind = audioChip.querySelector(".chip-kind");
    kind.textContent = state.audio_kind_text || "";
    kind.style.color = state.audio_clip_warning ? "var(--red)" : "";
  } else {
    audioChip.hidden = true;
    // Sin beat cargado no puede quedar sonando ninguno: quitarlo a mitad de
    // reproduccion dejaba el archivo viejo de largo (el <audio> es aparte del
    // canvas, ver el bloque del beat mas abajo).
    stopBeat();
  }

  $("#focus-section").hidden = !state.show_focus;
  // show_trim/show_speed se prenden apenas se elige un video, ANTES de que
  // termine de sondearse su duracion real (media_duration llega despues,
  // por _probe_video_job) -- sin este chequeo el slider de recorte
  // arrancaba con una duracion de relleno de 1s ("0:00 - 0:01"), un rango
  // practicamente degenerado donde la perilla izquierda no tenia a donde
  // moverse. Se esconden juntos hasta tener la duracion real.
  const videoReady = state.show_trim && !!state.media_duration;
  $("#trim-section").hidden = !videoReady;
  $("#speed-section").hidden = !videoReady;
  $("#scale-section").hidden = !state.show_scale;

  $("#generate-btn").disabled = !state.ready;
}

function handleResult(result) {
  if (result && result.state) {
    render(result.state);
  }
  if (result && result.ignored && result.ignored.length) {
    $("#status-text").textContent =
      "Ignorado (no es imagen, video ni audio): " + result.ignored.join(", ");
  }
}

// ------------------------------------------------------------ perillas
//
// El tramo ya recorrido de la barra (de la izquierda a la chibola) va del
// color de acento, igual que el pedazo seleccionado del recorte del loop.
// Ese recorte lo puede hacer con divs propios (.range-fill), pero estas tres
// perillas (Bordes del video, Opacidad y Escala) son <input type=range>
// nativos y Chromium no tiene pseudo-elemento para la parte llena: se pinta
// con un degradado de corte seco en el punto --fill -- ver .slider en
// styles.css. Hay que llamar a esto en CADA lugar que le cambie el valor a
// una perilla, si no la barra se queda toda gris.
function paintSliderFill(slider) {
  const min = Number(slider.min);
  const max = Number(slider.max);
  const span = max - min || 1;
  const frac = (Number(slider.value) - min) / span;
  slider.style.setProperty("--fill", `${(frac * 100).toFixed(1)}%`);
}

// -------------------------------------------------- tarjeta de galeria

// Rediseno: marco (borde propio) con la miniatura mas chica adentro
// (inset, con aire alrededor) y el nombre como texto plano DEBAJO del
// marco -- reemplaza el diseno anterior (miniatura a pantalla completa +
// nombre superpuesto con degradado de sombra encima).
function buildGalleryCard({ thumb, name, active, onClick, onDelete }) {
  const card = document.createElement("div");
  card.className = "gallery-card" + (active ? " active" : "");
  card.title = name;

  const frame = document.createElement("div");
  frame.className = "gallery-card-frame";

  const inner = document.createElement("div");
  inner.className = "gallery-card-thumb";
  if (thumb) inner.style.backgroundImage = `url(${thumb})`;
  frame.appendChild(inner);

  const del = document.createElement("button");
  del.className = "gallery-card-delete";
  del.type = "button";
  del.innerHTML = iconSvg("trash", 16, 2);
  del.title = "Eliminar archivo";
  del.addEventListener("click", (e) => {
    e.stopPropagation();
    onDelete();
  });
  frame.appendChild(del);
  card.appendChild(frame);

  const label = document.createElement("span");
  label.className = "gallery-card-name";
  label.textContent = name;
  card.appendChild(label);

  card.addEventListener("click", onClick);
  return card;
}

// Arrastrar-y-soltar un archivo sobre la seccion (#texture-section /
// #template-section) ya funciona sin nada extra aca -- ver drop-handler.js.
// La unica forma de agregar por click es el link "+ Añadir" del titulo de
// cada seccion (estilo Notion: nada de tarjeta/caja en el estado vacio).

// ----------------------------------------------------------- plantilla

function renderTemplateGallery(state) {
  const gallery = $("#template-gallery");
  gallery.innerHTML = "";
  templatesCache.templates.forEach((t) => {
    const active = state.template_path === t.path;
    gallery.appendChild(buildGalleryCard({
      thumb: t.thumb,
      name: t.name,
      active,
      onClick: () => toggleTemplate(t.path, active),
      onDelete: () => deleteTemplateFile(t.path, t.name),
    }));
  });
  renderTemplateInfo(state);
}

function renderTemplateInfo(state) {
  const el = $("#template-info");
  const hay = !!(state.template_path && state.template_box);
  if (hay) {
    const [x, y, w, h] = state.template_box;
    // El tamano de salida lo dice Python (ver canvas_size en get_state), no
    // esta escrito aca: asi la linea no vuelve a mentir si cambia el lienzo.
    const [cw, ch] = state.canvas_size || [];
    const salida = cw && ch ? ` · salida ${cw}x${ch}` : "";
    el.textContent =
      `Ventana transparente detectada: ${w}x${h} en (${x}, ${y})${salida}`;
  }
  // Aparece y desaparece con el mismo despliegue que el panel de texturas
  // (.layer-controls): la clase .is-collapsed en vez del atributo [hidden],
  // porque el [hidden] global es display:none y eso corta cualquier
  // transicion. El texto NO se borra al cerrar -- si se vaciara, el alto
  // llegaria a 0 de golpe y no habria nada que animar.
  el.hidden = false;
  el.classList.toggle("is-collapsed", !hay);
}

function browseTemplate() {
  pywebview.api.browse_template().then((result) => {
    if (!result.ok && !result.cancelled) {
      $("#status-text").textContent = result.error || "No se pudo usar esa plantilla.";
    }
    refreshTemplates().then(refresh);
  });
}

function toggleTemplate(path, active) {
  markPresetModified();
  if (active) {
    pywebview.api.clear_template().then(() => refresh());
    return;
  }
  pywebview.api.set_template(path).then((result) => {
    if (!result.ok) {
      $("#status-text").textContent = result.error || "No se pudo usar esa plantilla.";
    }
    refreshTemplates().then(refresh);
  });
}

function deleteTemplateFile(path, name) {
  if (!confirm(`¿Eliminar la plantilla "${name}"?`)) return;
  pywebview.api.delete_template_file(path).then(() => {
    refreshTemplates().then(refresh);
  });
}

// ------------------------------------------------------------ texturas

function renderTextureGallery(state) {
  const gallery = $("#texture-gallery");
  gallery.innerHTML = "";
  const layers = state.texture_layers || [];

  availableTextures.forEach((tex) => {
    const layerIndex = layers.findIndex((l) => l.path === tex.path);
    gallery.appendChild(buildGalleryCard({
      thumb: tex.thumb,
      name: tex.name,
      active: layerIndex !== -1,
      onClick: () => toggleTextureLayer(tex.path, layerIndex),
      onDelete: () => deleteTextureFile(tex.path),
    }));
  });

  renderTextureControls(state);
}

function browseTexture() {
  pywebview.api.browse_texture_file().then((path) => {
    if (!path) return;
    markPresetModified();
    pywebview.api.add_texture_layer(path).then(() => {
      selectedTexturePath = path;
      refreshAvailableTextures().then(refresh);
    });
  });
}

// El click en la tarjeta hace UNA de tres cosas, segun como este esa textura:
//
//   apagada                  -> se prende y queda elegida para editar
//   prendida, otra elegida   -> pasa a ser la elegida (NO se apaga)
//   prendida y ya elegida    -> se apaga
//
// El caso del medio es el que faltaba: con dos texturas puestas, el unico click
// disponible las quitaba, asi que no habia forma de pasar de una a la otra para
// cambiarle el estilo, la opacidad o la escala. Con una sola textura no cambia
// nada -- la que esta prendida es siempre la elegida, asi que el click la apaga
// igual que antes.
function toggleTextureLayer(path, layerIndex) {
  if (layerIndex === -1) {
    markPresetModified();
    pywebview.api.add_texture_layer(path).then(() => {
      selectedTexturePath = path;
      refresh();
    });
    return;
  }
  if (selectedTexturePath !== path) {
    // Solo cambia a cual le apuntan los controles: no toca las capas, asi que
    // no hay que ir a Python ni marcar el preset como modificado.
    selectedTexturePath = path;
    if (lastState) renderTextureControls(lastState);
    return;
  }
  markPresetModified();
  pywebview.api.remove_texture_layer(layerIndex).then(() => {
    selectedTexturePath = null;
    refresh();
  });
}

function deleteTextureFile(path) {
  const name = path.split(/[\\/]/).pop();
  if (!confirm(`¿Quitar la textura "${name}" de la app y de las capas que la usan?`)) return;
  pywebview.api.delete_texture_file(path).then((r) => {
    if (!r.ok) {
      $("#status-text").textContent = r.error || "No se pudo eliminar la textura.";
      return;
    }
    markPresetModified();
    if (selectedTexturePath === path) selectedTexturePath = null;
    refreshAvailableTextures().then(refresh);
  });
}

function currentTextureLayerIndex() {
  const layers = (lastState && lastState.texture_layers) || [];
  return layers.findIndex((l) => l.path === selectedTexturePath);
}

function renderTextureControls(state) {
  const panel = $("#texture-controls");
  const layers = state.texture_layers || [];
  let index = layers.findIndex((l) => l.path === selectedTexturePath);
  // Si hay capas activas SIEMPRE hay una seleccionada. Sin esto, al arrancar
  // la app (las capas se restauran de config.json, pero selectedTexturePath
  // nace en null) la tarjeta se veia encendida y el panel de abajo seguia
  // oculto: para editar opacidad/escala habia que apagar la textura y volver
  // a prenderla. Mismo caso al quitar una capa cuando quedan otras.
  if (index === -1 && layers.length) {
    index = 0;
    selectedTexturePath = layers[0].path;
  }
  // .is-collapsed y no el atributo [hidden]: styles.css tiene un
  // [hidden] { display: none !important } global, y display:none corta la
  // animacion de despliegue del panel.
  if (index === -1) {
    panel.classList.add("is-collapsed");
    return;
  }
  panel.classList.remove("is-collapsed");
  const layer = layers[index];
  setTextureBlendLabel(layer.blend);
  $("#texture-opacity-slider").value = layer.opacity;
  $("#texture-opacity-entry").value = layer.opacity;
  $("#texture-scale-slider").value = layer.scale;
  $("#texture-scale-entry").value = layer.scale;
  paintSliderFill($("#texture-opacity-slider"));
  paintSliderFill($("#texture-scale-slider"));
}

function commitTextureOpacity(v) {
  v = Math.max(0, Math.min(100, Math.round(Number(v) || 0)));
  $("#texture-opacity-slider").value = v;
  $("#texture-opacity-entry").value = v;
  paintSliderFill($("#texture-opacity-slider"));
  const i = currentTextureLayerIndex();
  if (i === -1) return;
  markPresetModified();
  pywebview.api.update_texture_layer(i, { opacity: v });
  Preview.schedulePreview();
  invalidateLoopPreview();
}

function commitTextureScale(v) {
  v = Math.max(10, Math.min(300, Math.round(Number(v) || 100)));
  $("#texture-scale-slider").value = v;
  $("#texture-scale-entry").value = v;
  paintSliderFill($("#texture-scale-slider"));
  const i = currentTextureLayerIndex();
  if (i === -1) return;
  markPresetModified();
  pywebview.api.update_texture_layer(i, { scale: v });
  Preview.schedulePreview();
  invalidateLoopPreview();
}

// --------------------------------------------------------------- escala
//
// El control se llama "Bordes" pero por dentro edita scale_pct, que es que
// tan ANCHO va el medio dentro del lienzo (ver build_layout en engine.py: el
// lienzo es 1920x1080, el alto del medio siempre 1080 y el ancho
// 1080 * scale_pct/100). O sea:
//   scale_pct 100 = cuadrado de 1080x1080 (el valor de fabrica)
//   scale_pct 178 = 1920 de ancho, llena el lienzo y no queda ni un borde
//   scale_pct  40 = 432 de ancho, el maximo de borde que se permite
//
// Lo que se MUESTRA es otra escala, en "cuanto borde hay", de 0 a 100:
//   0 = sin bordes | 50 = el cuadrado de siempre | 100 = borde maximo
// Antes se mostraba 240 - scale_pct, con dos problemas: el valor por defecto
// salia "140" (no significa nada para nadie, y es el numero que confundia) y
// de 40 a 62 no pasaba absolutamente NADA, porque el ancho ya venia topado
// en 1920 -- 22 puntos de perilla muertos. Son dos tramos rectos que se
// juntan en 50 para que el cuadrado caiga justo ahi; con un solo tramo
// recto el valor por defecto habria quedado en 57%. Python no se entera de
// nada de esto: sigue recibiendo y guardando scale_pct tal cual.
const SCALE_NO_BORDER = 178; // llena el lienzo (1080 * 1.78 ya pasa de 1920)
const SCALE_SQUARE = 100;    // el cuadrado 1080x1080
const SCALE_MAX_BORDER = 40; // lo mas angosto que se deja

function uiToScalePct(ui) {
  ui = Math.max(0, Math.min(100, ui));
  if (ui <= 50) return SCALE_NO_BORDER - (ui / 50) * (SCALE_NO_BORDER - SCALE_SQUARE);
  return SCALE_SQUARE - ((ui - 50) / 50) * (SCALE_SQUARE - SCALE_MAX_BORDER);
}

function scalePctToUi(pct) {
  const value = pct >= SCALE_SQUARE
    ? ((SCALE_NO_BORDER - pct) / (SCALE_NO_BORDER - SCALE_SQUARE)) * 50
    : 50 + ((SCALE_SQUARE - pct) / (SCALE_SQUARE - SCALE_MAX_BORDER)) * 50;
  // Un preset viejo puede traer un scale_pct de fuera del rango nuevo (por
  // ejemplo 200, que se veia igual que 178).
  return Math.max(0, Math.min(100, Math.round(value)));
}

function renderScale(state) {
  const uiValue = scalePctToUi(state.scale_pct);
  $("#scale-slider").value = uiValue;
  $("#scale-entry").value = uiValue;
  paintSliderFill($("#scale-slider"));
  // El control tambien aplica a fotos sueltas (sin plantilla, ver
  // show_scale en api.py) -- "Bordes del video" seria incorrecto ahi.
  $("#scale-section-title").textContent = state.media_is_video ? "Bordes del video" : "Bordes de la imagen";
}

function commitScale(v) {
  // Ojo con el 0: "Number(v) || 50" lo convertiria en 50 (sin bordes es un
  // valor perfectamente valido).
  const n = Math.round(Number(v));
  v = Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 50;
  $("#scale-slider").value = v;
  $("#scale-entry").value = v;
  paintSliderFill($("#scale-slider"));
  markPresetModified();
  pywebview.api.set_scale_pct(Math.round(uiToScalePct(v)));
  Preview.schedulePreview();
  // Sin esto el fotograma estatico se actualizaba con el borde nuevo pero el
  // loop seguia con el borde viejo. No lo rearma en el momento: lo deja
  // vencido y se compone cuando se lo quiera ver (ver invalidateLoopPreview).
  invalidateLoopPreview();
}

// ----------------------------------------------------------- velocidad

function renderSpeedControl(state) {
  $$(".segmented-item").forEach((btn) => {
    btn.classList.toggle("segmented-active", btn.dataset.value === state.speed);
  });
}

// -------------------------------------------------------------- presets
//
// Dropdown propio (no <select> nativo) para poder mostrar los iconos de
// renombrar/eliminar en cada fila cuando esta desplegado. El nombre se
// escribe en un input en linea con el estilo de la app -- nada de
// prompt()/confirm() nativos del navegador salvo la confirmacion de
// borrado (ahi si es apropiado, es una decision simple de si/no).

let currentPresetName = null;   // ultimo preset aplicado (solo para el texto del trigger)
let presetDropdownOpen = false;
let presetRenamingName = null;  // si no es null, esa fila muestra el input de renombrar

// "Modificado desde que se aplico/guardo": bandera simple en vez de
// comparar snapshots -- los sliders de textura/bordes (opacidad, escala,
// "Bordes de la imagen") a proposito NO disparan un refresh() de pantalla
// completa al arrastrar (ver el comentario en commitTextureOpacity), asi
// que una comparacion de estado en render() nunca los hubiera visto.
// markPresetModified() se llama a mano en cada punto que toca un campo
// que un preset guarda (plantilla, texturas, bordes, velocidad).
let presetModified = false;

function markPresetModified() {
  if (!currentPresetName || presetModified) return;
  presetModified = true;
  if (lastState) renderPresets(lastState); // repinta el asterisco ya mismo, sin esperar un refresh()
}

function clearPresetModified() {
  presetModified = false;
}

function renderPresets(state) {
  const names = state.presets || [];
  if (currentPresetName && !names.includes(currentPresetName)) {
    currentPresetName = null; // se borro o ya no existe
    presetModified = false;
  }
  $("#preset-trigger-label").textContent = names.length
    ? (currentPresetName ? `${currentPresetName}${presetModified ? " *" : ""}` : "Elegir preset")
    : "(sin presets guardados)";
  renderPresetList(names);
}

function renderPresetList(names) {
  const list = $("#preset-list");
  list.innerHTML = "";
  names.forEach((name) => list.appendChild(buildPresetRow(name)));
}

function buildPresetRow(name) {
  const row = document.createElement("div");
  row.className = "preset-dropdown-item";

  if (presetRenamingName === name) {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "preset-inline-input";
    input.value = name;
    const commit = () => {
      const newName = input.value.trim();
      presetRenamingName = null;
      if (!newName || newName === name) {
        renderPresetList((lastState && lastState.presets) || []);
        return;
      }
      pywebview.api.rename_preset(name, newName).then((r) => {
        if (!r.ok) {
          $("#status-text").textContent = r.error;
          return;
        }
        if (currentPresetName === name) currentPresetName = newName;
        refresh();
      });
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commit();
      if (e.key === "Escape") {
        presetRenamingName = null;
        renderPresetList((lastState && lastState.presets) || []);
      }
    });
    input.addEventListener("blur", commit);
    row.appendChild(input);
    setTimeout(() => {
      input.focus();
      input.select();
    }, 0);
    return row;
  }

  const label = document.createElement("span");
  label.className = "preset-dropdown-item-name";
  label.textContent = name;
  label.addEventListener("click", () => applyPreset(name));
  row.appendChild(label);

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "preset-dropdown-item-icon";
  editBtn.innerHTML = iconSvg("edit");
  editBtn.title = "Renombrar";
  editBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    presetRenamingName = name;
    renderPresetList((lastState && lastState.presets) || []);
  });
  row.appendChild(editBtn);

  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "preset-dropdown-item-icon";
  delBtn.innerHTML = iconSvg("trash");
  delBtn.title = "Eliminar";
  delBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!confirm(`¿Eliminar el preset "${name}"?`)) return;
    pywebview.api.delete_preset(name).then(() => {
      if (currentPresetName === name) currentPresetName = null;
      refresh();
    });
  });
  row.appendChild(delBtn);

  return row;
}

function applyPreset(name) {
  pywebview.api.apply_preset(name).then((r) => {
    if (!r.ok) {
      $("#status-text").textContent = r.error || "No se pudo aplicar el preset.";
      return;
    }
    currentPresetName = name;
    clearPresetModified();
    closePresetDropdown();
    // Selecciona la primera capa restaurada -- igual que onTextureAdded
    // hace para el drag&drop (ver ese comentario): sin esto
    // selectedTexturePath se queda en lo que hubiera antes (o null), la
    // tarjeta aparece activa pero el panel de opacidad/escala de abajo
    // sigue oculto -- no hay forma de editar la textura que acaba de
    // cargar el preset.
    const layers = r.state.texture_layers || [];
    selectedTexturePath = layers.length ? layers[0].path : null;
    render(r.state);
    refreshTemplates();
    // Sin encadenar el refresh, la galeria de texturas se repintaba con la
    // lista VIEJA de availableTextures (la de antes de aplicar el preset):
    // la textura del preset quedaba cargada de verdad (state.texture_layers
    // la tenia), pero ninguna tarjeta se marcaba activa porque
    // renderTextureGallery cruza availableTextures con texture_layers, y
    // availableTextures todavia no incluia esa textura. Se veia como si la
    // textura hubiera desaparecido al aplicar el preset.
    refreshAvailableTextures().then(refresh);
    $("#status-text").textContent = r.missing && r.missing.length
      ? `Preset "${name}" aplicado — no se encontró la ${r.missing.join("/")} guardada`
      : `Preset aplicado: ${name}`;
    $("#status-text").style.color = r.missing && r.missing.length ? "var(--red)" : "var(--accent)";
  });
}

// Las listas de dropdown (presets, modo de mezcla) viven adentro de
// .control-panel, que tiene overflow-y:auto -- un position:absolute
// normal quedaba recortado ahi si se abrian cerca del borde del panel.
// Esto las saca del flujo por completo (las muda a <body>, position:fixed
// calculado a mano) y decide para arriba/para abajo segun el espacio real
// que queda en la ventana.
// Ancho minimo de una lista desplegada: 236px es el ancho de los campos del
// panel, asi que la lista de Preset (cuyo disparador mide 196 -- al lado
// tiene el boton de guardar) termina justo alineada con el borde derecho de
// los demas campos. Con el ancho del disparador a secas, los nombres largos
// se partian en dos lineas, y el desplegable de Estilo de la textura (un
// disparador que mide lo que su texto, ~85px) salia mucho mas angosto que
// sus propias opciones.
const DROPDOWN_MIN_WIDTH = 236;

function positionFloatingDropdown(trigger, list) {
  if (list.parentElement !== document.body) document.body.appendChild(list);
  const margin = 6;
  const rect = trigger.getBoundingClientRect();
  const width = Math.max(rect.width, DROPDOWN_MIN_WIDTH);
  list.style.position = "fixed";
  // Al ser mas ancha que el disparador, una lista abierta cerca del borde
  // derecho se saldria de la ventana -- se corre lo justo para que entre.
  list.style.left = `${Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin))}px`;
  list.style.width = `${width}px`;
  list.style.right = "auto";
  list.style.bottom = "auto";
  list.style.top = `${rect.bottom + margin}px`;

  const spaceBelow = window.innerHeight - rect.bottom - margin;
  const spaceAbove = rect.top - margin;
  const naturalHeight = list.scrollHeight + 2; // + borde
  if (naturalHeight > spaceBelow && spaceAbove > spaceBelow) {
    list.style.top = "auto";
    list.style.bottom = `${window.innerHeight - rect.top + margin}px`;
    list.style.maxHeight = `${spaceAbove}px`;
  } else {
    list.style.maxHeight = `${spaceBelow}px`;
  }
}

function togglePresetDropdown() {
  if (!presetDropdownOpen && !(lastState && lastState.presets.length)) return;
  presetDropdownOpen = !presetDropdownOpen;
  presetRenamingName = null;
  const list = $("#preset-list");
  $("#preset-dropdown").classList.toggle("open", presetDropdownOpen);
  if (presetDropdownOpen) {
    list.hidden = false;
    positionFloatingDropdown($("#preset-trigger"), list);
  }
  animateDropdown(list, presetDropdownOpen);
}

function closePresetDropdown() {
  if (!presetDropdownOpen) return;
  presetDropdownOpen = false;
  presetRenamingName = null;
  $("#preset-dropdown").classList.remove("open");
  animateDropdown($("#preset-list"), false);
}

// ------------------------------------------- dropdown de modo de mezcla
// Mismo componente visual que el dropdown de presets (en vez del <select>
// nativo, cuyo listado de opciones el sistema operativo pinta blanco sin
// forma de aplicarle el vidrio del resto de la UI).
let textureBlendOpen = false;

function setTextureBlendLabel(value) {
  $("#texture-blend-label").textContent = value;
  $$("#texture-blend-list .preset-dropdown-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.value === value);
  });
}

function toggleTextureBlendDropdown() {
  textureBlendOpen = !textureBlendOpen;
  const list = $("#texture-blend-list");
  $("#texture-blend-dropdown").classList.toggle("open", textureBlendOpen);
  if (textureBlendOpen) {
    list.hidden = false;
    positionFloatingDropdown($("#texture-blend-trigger"), list);
  }
  animateDropdown(list, textureBlendOpen);
}

function closeTextureBlendDropdown() {
  if (!textureBlendOpen) return;
  textureBlendOpen = false;
  $("#texture-blend-dropdown").classList.remove("open");
  animateDropdown($("#texture-blend-list"), false);
}

// -------------------------------------------- guardar preset (input en linea)

// Con un preset activo y modificado (asterisco), el boton de guardar no
// salta directo al input de nombre nuevo -- primero pregunta si es una
// actualizacion del preset actual o uno nuevo aparte. Sin esto, guardar
// los ajustes tocados sobre el MISMO preset significaba borrar el nombre
// ya escrito y volver a teclearlo solo para disparar el "sobrescribir?".
function handleSaveClick() {
  if (currentPresetName && presetModified) {
    showSaveChoice();
  } else {
    startSaveNewPreset();
  }
}

function showSaveChoice() {
  closePresetDropdown();
  $("#preset-overwrite-name").textContent = currentPresetName;
  $("#preset-controls").hidden = true;
  $("#preset-save-choice").hidden = false;
}

function overwriteCurrentPreset() {
  const name = currentPresetName;
  pywebview.api.save_preset(name).then((r) => {
    if (!r.ok) {
      $("#status-text").textContent = r.error;
      return;
    }
    clearPresetModified();
    $("#status-text").textContent = `Preset actualizado: ${name}`;
    $("#status-text").style.color = "var(--accent)";
    resetPresetSaveUI();
    refresh();
  });
}

function startSaveNewPreset() {
  closePresetDropdown();
  $("#preset-controls").hidden = true;
  $("#preset-save-choice").hidden = true;
  $("#preset-save-row").hidden = false;
  const input = $("#preset-save-input");
  input.value = "";
  input.focus();
}

function resetPresetSaveUI() {
  $("#preset-controls").hidden = false;
  $("#preset-save-choice").hidden = true;
  $("#preset-save-row").hidden = true;
}

// alias -- el input de "guardar como nuevo" ya llamaba a esta funcion para
// cancelar (Escape / boton Cancelar); se mantiene el nombre para no tocar
// esos call sites.
function cancelSaveNewPreset() {
  resetPresetSaveUI();
}

function confirmSaveNewPreset() {
  const name = $("#preset-save-input").value.trim();
  if (!name) {
    cancelSaveNewPreset();
    return;
  }
  if (lastState && lastState.presets.includes(name) &&
      !confirm(`Ya existe un preset llamado "${name}". ¿Sobrescribirlo?`)) {
    return;
  }
  pywebview.api.save_preset(name).then((r) => {
    if (!r.ok) {
      $("#status-text").textContent = r.error;
      return;
    }
    currentPresetName = name;
    // El preset se guarda con el estado ACTUAL (ver save_preset en
    // api.py) -- ya coincide con si mismo, asi que arranca sin asterisco.
    clearPresetModified();
    $("#status-text").textContent = `Preset guardado: ${name}`;
    $("#status-text").style.color = "var(--accent)";
    cancelSaveNewPreset();
    refresh();
  });
}

// ------------------------------------------------------- titulo / salida

// Las rutas se muestran cortadas a sus ultimos tramos: el "C:\Users\erick\
// Desktop\" de adelante no dice nada y se comia todo el ancho visible del
// campo, que ademas recorta con puntos suspensivos AL FINAL -- o sea que lo
// unico que se llegaba a leer era la parte inutil. La ruta completa queda en
// el title (al pasar el cursor).
function shortPath(path, maxParts = 3) {
  if (!path) return "";
  const parts = String(path).split(/[\\/]+/).filter(Boolean);
  if (parts.length <= maxParts) return parts.join("\\");
  return `…\\${parts.slice(-maxParts).join("\\")}`;
}

function renderOutput(state) {
  if (document.activeElement !== $("#project-title")) {
    $("#project-title").value = state.custom_output_name || "";
  }
  const label = $("#output-label");
  label.textContent = shortPath(state.output_path) || "Ruta donde se almacenará el video";
  label.title = state.output_path || "Cambiar";
}

// ---------------------------------------------------------- generacion

// La tarjeta de resultado sale al terminar de exportar y se queda puesta: es
// "lo ultimo que exportaste", y sus tres acciones (abrir la carpeta, ver el
// video, guardar la portada) siguen sirviendo mientras ese archivo exista. Se
// va al arrancar otra exportacion, o al cargar otro medio -- ahi Python apaga
// cover_available (ver _set_image/_set_video/remove_media) y render() la baja.
function setExportResult(filename) {
  if (filename) $("#result-card-name").textContent = filename;
  $("#result-card").hidden = !filename;
}

function setGeneratingUI(active) {
  $("#generate-btn").disabled = active || !(lastState && lastState.ready);
  $("#cancel-btn").hidden = !active;
  // Mientras se exporta, el boton de generar no tiene nada que hacer ahi: se
  // va del todo (no deshabilitado y ocupando lugar) y en su fila queda
  // "Cancelar". Vuelve al terminar, debajo de la tarjeta del resultado.
  $(".generate-row").hidden = active;
  if (active) {
    // El resultado del export anterior ya no aplica (su portada tampoco).
    setExportResult(null);
    $("#progress-bar").hidden = false;
    $("#progress-fill").style.width = "0%";
  }
}

function beginGeneration() {
  pywebview.api.start_generation().then((r) => {
    if (!r.ok) {
      // Se re-habilita: quedo deshabilitado desde el click (ver arriba),
      // pero esta generacion en particular no arranco de verdad.
      $("#generate-btn").disabled = !(lastState && lastState.ready);
      $("#status-text").textContent = r.error || "No se pudo iniciar la generación.";
      return;
    }
    setGeneratingUI(true);
    $("#status-text").textContent = "Preparando...";
    $("#status-text").style.color = "";
  });
}

window.onProgress = (frac) => {
  $("#progress-fill").style.width = `${Math.round(frac * 100)}%`;
};

window.onStatus = (payload) => {
  $("#status-text").textContent = payload.text;
};

// Progreso de la descarga por link (yt-dlp, ver _push_download_status en
// api.py) -- evento aparte de onStatus, que es de la generacion real (la
// barra de progreso de mas abajo).
window.onDownloadStatus = (payload) => {
  $("#download-status").textContent = payload.text;
  $("#download-status").style.color = payload.color || "";
};

window.onJobDone = (payload) => {
  setGeneratingUI(false);
  // Salio bien: lo cuenta la tarjeta de resultado, no el texto ni la barra
  // llena -- dejar las tres cosas era lo que amontonaba el pie. Si fallo (o
  // se cancelo) sigue siendo el texto el que avisa, con su color.
  $("#progress-bar").hidden = true;
  if (payload.ok) {
    $("#status-text").textContent = "";
    $("#status-text").style.color = "";
    $("#save-cover-btn").hidden = !payload.cover_available;
    setExportResult(payload.filename || payload.message || "video exportado");
    return;
  }
  $("#status-text").textContent = payload.message;
  $("#status-text").style.color = payload.cancelled ? "" : "var(--red)";
};

window.onJobError = (payload) => {
  setGeneratingUI(false);
  $("#progress-bar").hidden = true;
  $("#status-text").textContent = "Error inesperado.";
  $("#status-text").style.color = "var(--red)";
  alert(payload.message);
};

// Python empuja el estado cuando termina un trabajo en segundo plano
// (sondeo de video/audio, drop de plantilla/textura, etc.) -- reemplazo
// de self.after(0, ...). Un drop de plantilla/textura cambia el estado
// SIN pasar por browse_template()/add_texture_layer() del lado JS, asi
// que aca tambien hay que refrescar los caches de galeria o la tarjeta
// nueva nunca aparece aunque el backend ya la haya guardado.
window.onStateChanged = function (state) {
  Promise.all([
    pywebview.api.list_templates(),
    pywebview.api.list_available_textures(),
  ]).then(([templates, textures]) => {
    templatesCache = templates;
    availableTextures = textures;
    render(state);
  });
};

// Soltar una textura arrastrada la agrega en Python sin pasar por
// toggleTextureLayer() -- sin esto "selectedTexturePath" (variable de UI,
// solo vive en JS) se quedaba en null y la tarjeta aparecia activa pero
// sin los controles de opacidad/escala debajo.
window.onTextureAdded = function (path) {
  selectedTexturePath = path;
};

// ------------------------------------------------------- link (yt-dlp)

// El campo del link se deshabilita mientras baja, pero eso no alcanza para
// que no se dispare dos veces: con el campo deshabilitado el foco se va al
// body y ahi el Ctrl+V global (que se abstiene solo cuando el foco esta en
// un INPUT) vuelve a llamar aca. Dos descargas a la vez se pisan el archivo
// temporal y mezclan sus porcentajes en el cartel -- ver el candado gemelo
// en download_from_link (api.py), que ademas cubre el caso desde Python.
let downloading = false;

function startDownload(url) {
  if (downloading) return;
  if (!/^https?:\/\//i.test(url)) {
    $("#download-status").textContent = "Pega un link válido (que empiece con https://).";
    $("#download-status").style.color = "var(--red)";
    return;
  }
  downloading = true;
  $("#link-input").disabled = true;
  $("#download-status").textContent = "Descargando video...";
  $("#download-status").style.color = "";
  pywebview.api.download_from_link(url).then((r) => {
    if (!r.ok) {
      downloading = false;
      $("#link-input").disabled = false;
      $("#download-status").textContent = r.error;
      $("#download-status").style.color = "var(--red)";
    }
  });
}

window.onDownloadDone = (payload) => {
  downloading = false;
  $("#link-input").disabled = false;
  if (payload.ok) {
    // El chip que aparece en "Contenido del video" ya confirma que
    // funciono -- no hace falta ademas un aviso de texto redundante, pero
    // si hay que limpiar el "Descargando video..." que se puso al
    // arrancar la descarga (startDownload), o se quedaba pegado para
    // siempre.
    $("#link-input").value = "";
    $("#download-status").textContent = "";
    refresh();
    return;
  }
  $("#download-status").textContent = payload.message;
  $("#download-status").style.color = "var(--red)";
};

// ---------------------------------------------- resaltado de zonas de drop
//
// dragenter/dragover/dragleave son 100% visuales (solo prenden/apagan una
// clase CSS) y no necesitan pasar por Python -- se manejan aca con
// addEventListener nativo, sincrono, para que nunca queden desordenados.
// El "drop" real (que si necesita leer pywebviewFullPath) sigue en Python
// (Api._bind_drop_zone). stopPropagation() evita que arrastrar sobre
// Plantilla/Texturas tambien prenda el resaltado del body/dropzone
// general por debajo.
function setupDragHighlight(selector) {
  const el = document.querySelector(selector);
  if (!el) return;
  let depth = 0;
  el.addEventListener("dragenter", (e) => {
    e.stopPropagation();
    depth++;
    el.classList.add("drag-active");
  });
  el.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.stopPropagation();
  });
  el.addEventListener("dragleave", (e) => {
    e.stopPropagation();
    depth = Math.max(depth - 1, 0);
    if (depth === 0) el.classList.remove("drag-active");
  });
  el.addEventListener("drop", (e) => {
    e.stopPropagation();
    depth = 0;
    el.classList.remove("drag-active");
  });
}

// ---------------------------------------------------------------- init

window.addEventListener("pywebviewready", () => {
  fillStaticIcons();
  // Gris neutro desde el primer pintado -- sin esto --accent se quedaba en
  // el morado fijo de :root (styles.css) hasta el primer refresh() con
  // hasMedia=false, que tarda lo que tarden refreshTemplates()/
  // refreshAvailableTextures() en resolver.
  if (window.setAdaptiveAccent) window.setAdaptiveAccent(null);
  FocusPicker.init();
  LoopSlider.init();
  Preview.init();
  // Antes de initLoopPreviewControls, que le pide el elemento del canvas.
  LivePreview.init();
  $("#loop-preview-play").addEventListener("click", playLoopPreview);
  $("#preview-toggle-btn").addEventListener("click", togglePreviewPanel);
  initLoopPreviewControls();

  // Nada de adentro de la app se arrastra: el drag&drop es SOLO para
  // archivos que vienen de afuera (dragstart solo dispara para arrastres que
  // empiezan dentro de la pagina, asi que esto no toca los drops de
  // archivos). Ultimo cinturon contra el arrastre nativo que se colaba a
  // mitad de un gesto en las perillas -- ver el pointerdown de LoopSlider.
  document.addEventListener("dragstart", (e) => e.preventDefault());

  ["body", "#preview-dropzone", "#template-section", "#texture-section", "#cover-preview"]
    .forEach(setupDragHighlight);

  Promise.all([refreshTemplates(), refreshAvailableTextures()]).then(refresh);

  // ------------------------------------------ plantilla / texturas: "+ Añadir"
  $("#template-add-btn").addEventListener("click", browseTemplate);
  $("#texture-add-btn").addEventListener("click", browseTexture);

  // ------------------------------------------------------- titulo
  $("#project-title").addEventListener("input", (e) => {
    pywebview.api.set_output_name(e.target.value).then((path) => {
      $("#output-label").textContent = path || "Ruta donde se almacenará el video";
    });
  });
  // ------------------------------------------------------- archivos
  $("#remove-media-btn").addEventListener("click", () => {
    pywebview.api.remove_media().then((r) => render(r.state));
  });
  $("#rotate-media-btn").addEventListener("click", (e) => {
    // Deshabilitado durante el giro (no solo el guard de Python): sin esto,
    // clickear varias veces rapido mientras el primer giro todavia corre
    // mandaba pedidos que Python rechazaba con {ok:false} y SIN "state" --
    // render(undefined) explotaba.
    const btn = e.currentTarget;
    btn.disabled = true;
    pywebview.api.rotate_media().then((r) => {
      if (r.ok) render(r.state);
      btn.disabled = false;
    });
  });
  $("#audio-chip").querySelector(".btn-icon").addEventListener("click", () => {
    pywebview.api.remove_audio().then((r) => render(r.state));
  });

  // El evento "paste" (no keydown) da acceso directo a clipboardData: si
  // hay texto y es un link, se descarga sin pasar por Python; si no, se
  // delega en Python (imagen/archivos, via PIL.ImageGrab / NSPasteboard).
  document.addEventListener("paste", (e) => {
    // Con el panel de portada abierto, pegar una imagen la toma como PORTADA
    // en vez de cargarla como medio de la app. Python la deja en un PNG
    // temporal y devuelve la ruta (clipboard_image_path) -- del portapapeles
    // sale un bitmap, no un archivo, y ffmpeg necesita una ruta.
    if (!$("#cover-modal").hidden) {
      e.preventDefault();
      pywebview.api.clipboard_image_path().then((r) => {
        if (r && r.ok && r.path) {
          setCoverImage(r.path);
          return;
        }
        coverSource = "image";
        coverImagePath = null;
        $("#cover-drop-hint").textContent = r && r.error
          ? r.error
          : "El portapapeles no tiene ninguna imagen.";
        renderCoverModal();
      });
      return;
    }
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA") return;
    const text = ((e.clipboardData && e.clipboardData.getData("text/plain")) || "").trim();
    if (/^https?:\/\//i.test(text)) {
      $("#link-input").value = text;
      startDownload(text);
      return;
    }
    pywebview.api.paste_from_clipboard().then((result) => {
      if (result && result.empty) {
        $("#status-text").textContent = "El portapapeles no tiene una imagen ni archivos.";
        return;
      }
      if (result && !result.ok) {
        $("#status-text").textContent = result.error || "No se pudo pegar.";
        return;
      }
      handleResult(result);
    });
  });

  // ------------------------------------------------------- link (yt-dlp)
  //
  // El boton de descargar se quito (ver conversacion del rediseno) --
  // pegar un link directo en el campo arranca la descarga sola, sin
  // esperar click/Enter. Mismo criterio (https://) que el paste global de
  // arriba, que se abstiene cuando el foco esta en un INPUT.
  $("#link-input").addEventListener("paste", (e) => {
    const text = ((e.clipboardData && e.clipboardData.getData("text/plain")) || "").trim();
    if (/^https?:\/\//i.test(text)) {
      e.preventDefault();
      $("#link-input").value = text;
      startDownload(text);
    }
  });
  $("#link-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") startDownload($("#link-input").value.trim());
  });

  // Nota: agregar plantilla/textura se dispara desde el link "+ Añadir"
  // del titulo de cada seccion (browseTemplate/browseTexture, mas arriba)
  // -- ver el listener de #template-add-btn/#texture-add-btn.

  // -------------------------------------------------------- texturas
  $("#texture-blend-trigger").addEventListener("click", (e) => {
    e.stopPropagation();
    toggleTextureBlendDropdown();
  });
  document.addEventListener("click", (e) => {
    // La lista se muda a <body> al abrirse (ver positionFloatingDropdown),
    // asi que closest("#texture-blend-dropdown") solo, ya no la encuentra
    // -- hay que chequear tambien closest("#texture-blend-list") aparte.
    if (textureBlendOpen && !e.target.closest("#texture-blend-dropdown") && !e.target.closest("#texture-blend-list")) {
      closeTextureBlendDropdown();
    }
  });
  $$("#texture-blend-list .preset-dropdown-item").forEach((item) => {
    item.addEventListener("click", () => {
      const value = item.dataset.value;
      setTextureBlendLabel(value);
      closeTextureBlendDropdown();
      const i = currentTextureLayerIndex();
      if (i === -1) return;
      markPresetModified();
      pywebview.api.update_texture_layer(i, { blend: value });
      Preview.schedulePreview();
    });
  });
  // El dropdown abierto queda position:fixed a las coordenadas del trigger
  // en el momento de abrirse -- si el panel scrollea (el trigger se mueve
  // debajo suyo), la lista se queda flotando en el lugar viejo. Mas simple
  // cerrarla que reposicionarla en cada evento de scroll.
  $(".control-panel").addEventListener("scroll", () => {
    closePresetDropdown();
    closeTextureBlendDropdown();
  });

  $("#texture-opacity-slider").addEventListener("input", (e) => commitTextureOpacity(e.target.value));
  $("#texture-opacity-entry").addEventListener("change", (e) => commitTextureOpacity(e.target.value));
  $("#texture-opacity-entry").addEventListener("keydown", (e) => {
    if (e.key === "Enter") commitTextureOpacity(e.target.value);
  });
  $("#texture-scale-slider").addEventListener("input", (e) => commitTextureScale(e.target.value));
  $("#texture-scale-entry").addEventListener("change", (e) => commitTextureScale(e.target.value));
  $("#texture-scale-entry").addEventListener("keydown", (e) => {
    if (e.key === "Enter") commitTextureScale(e.target.value);
  });

  // ------------------------------------------------------- velocidad
  $("#speed-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segmented-item");
    if (!btn) return;
    markPresetModified();
    pywebview.api.set_speed(btn.dataset.value).then(() => refresh());
    scheduleLoopPreview();
  });

  // ---------------------------------------------------------- escala
  $("#scale-slider").addEventListener("input", (e) => commitScale(e.target.value));
  $("#scale-entry").addEventListener("change", (e) => commitScale(e.target.value));
  $("#scale-entry").addEventListener("keydown", (e) => {
    if (e.key === "Enter") commitScale(e.target.value);
  });




  // --------------------------------------------------------- presets
  $("#preset-trigger").addEventListener("click", (e) => {
    e.stopPropagation();
    togglePresetDropdown();
  });
  document.addEventListener("click", (e) => {
    // Ver el comentario analogo en el listener de texture-blend-list: la
    // lista se muda a <body> al abrirse, asi que hay que chequear su
    // propio closest() aparte del wrapper original.
    if (presetDropdownOpen && !e.target.closest("#preset-dropdown") && !e.target.closest("#preset-list")) {
      closePresetDropdown();
    }
  });
  $("#preset-save").addEventListener("click", handleSaveClick);
  $("#preset-overwrite-btn").addEventListener("click", overwriteCurrentPreset);
  $("#preset-save-as-new-btn").addEventListener("click", startSaveNewPreset);
  $("#preset-save-choice-cancel").addEventListener("click", resetPresetSaveUI);
  $("#preset-save-confirm").addEventListener("click", confirmSaveNewPreset);
  $("#preset-save-cancel").addEventListener("click", cancelSaveNewPreset);
  $("#preset-save-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") confirmSaveNewPreset();
    if (e.key === "Escape") cancelSaveNewPreset();
  });

  // ------------------------------------------------- direccion de salida
  //
  // #output-label es el unico disparador (el boton aparte se quito, ver
  // conversacion del rediseno) -- role="button"+tabindex en index.html lo
  // anuncia como boton, asi que Enter/Espacio tienen que abrir el dialogo
  // igual que el click.
  const browseOutputPath = () => {
    pywebview.api.choose_output_path().then((path) => {
      $("#output-label").textContent = shortPath(path) || "Ruta donde se almacenará el video";
      $("#output-label").title = path || "Cambiar";
      // El dialogo nativo de "Guardar como" deja escribir el nombre del
      // archivo ahi mismo -- set_chosen_output (api.py) ya sincroniza
      // custom_output_name con eso, pero hace falta refresh() para que
      // "Nombre del video" recoja ese valor nuevo (path por si solo no
      // alcanza, la logica de que nombre corresponde vive en Python).
      refresh();
    });
  };
  $("#output-label").addEventListener("click", browseOutputPath);
  $("#output-label").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      browseOutputPath();
    }
  });

  // --------------------------------------------------------- generacion
  $("#generate-btn").addEventListener("click", () => {
    const btn = $("#generate-btn");
    // Se deshabilita ACA, sincronico, antes de cualquier await -- todo lo
    // de abajo (output_would_overwrite, el confirm(), start_generation) es
    // asincronico, y setGeneratingUI(true) recien llega DESPUES de que
    // start_generation responde. Sin este disabled inmediato, clics
    // repetidos mientras tanto disparaban 2-3 generaciones a la vez (ver
    // el guard/_generating en start_generation, api.py).
    if (btn.disabled) return;
    btn.disabled = true;
    pywebview.api.output_would_overwrite().then((wouldOverwrite) => {
      if (wouldOverwrite) {
        const name = $("#output-label").textContent;
        if (!confirm(`${name} ya existe. ¿Deseas reemplazarlo?`)) {
          btn.disabled = !(lastState && lastState.ready);
          return;
        }
      }
      beginGeneration();
    });
  });
  $("#cancel-btn").addEventListener("click", () => {
    pywebview.api.cancel_generation();
    $("#status-text").textContent = "Cancelando...";
  });
  $("#open-video-btn").addEventListener("click", () => pywebview.api.open_video());
  $("#open-folder-btn").addEventListener("click", () => pywebview.api.open_output_folder());
  // Se abre al pasar el cursor (sin click), pero YA NO se cierra al sacarlo:
  // ahora dentro se arrastran imagenes, y para eso hay que salir de la app a
  // buscar el archivo -- con el cierre por mouseleave (220ms) el panel ya no
  // estaba cuando volvias con el archivo en la mano. Se cierra con la X, con
  // Cancelar, con Escape o clickeando afuera, que es lo que corresponde a un
  // panel con controles adentro. El click en el boton sigue siendo el atajo
  // instantaneo, sin esperar el retraso del hover.
  $("#save-cover-btn").addEventListener("mouseenter", scheduleCoverModalOpen);
  $("#save-cover-btn").addEventListener("mouseleave", cancelCoverModalTimers);
  $("#save-cover-btn").addEventListener("click", () => {
    cancelCoverModalTimers();
    openCoverModal();
  });

  // ------------------------------------------------- modal "Guardar portada"
  // La barra recorre TODO el video, no el pedazo que hace loop: mueve el
  // previsualizador en vivo (que ya tiene el archivo cargado y lo compone) y el
  // panel muestra un espejo de ese lienzo.
  $("#cover-frame-seek").addEventListener("input", (e) => {
    // Mismo motivo que la barra del previsualizador: el relleno se pinta a mano
    // desde que la barra dejo de usar la apariencia nativa del navegador.
    paintSliderFill(e.target);
    const v = coverVideoEl();
    const largo = v.duration || 0;
    if (largo) v.currentTime = (e.target.value / 1000) * largo;
  });
  // ---- volver al fotograma del video ----
  $("#cover-back-btn").addEventListener("click", () => {
    coverSource = "frame";
    coverImagePath = null;
    $("#cover-image-preview").removeAttribute("src");
    if (lastState && lastState.media_is_video) {
      setupCoverFramePicker();
    } else if (lastState && !lastState.media_is_video) {
      $("#cover-image-preview").src = $("#preview-image").src;
    }
    renderCoverModal();
  });

  // ---- imagen propia: soltarla en el mini preview o buscarla con un clic ----
  const coverPreview = $("#cover-preview");
  coverPreview.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.stopPropagation();
  });
  coverPreview.addEventListener("drop", (e) => {
    // stopPropagation: sin esto el drop sube al del <body> y la imagen entra
    // como MEDIO de la app, reemplazando el video cargado (drop-handler.js).
    e.preventDefault();
    e.stopPropagation();
    coverPreview.classList.remove("drag-active");
    const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
    const path = files.map((f) => window.api.getPathForFile(f)).find((p) => p && isCoverImage(p));
    if (!path) {
      $("#cover-drop-hint").textContent = "Eso no es una imagen.";
      return;
    }
    setCoverImage(path);
  });
  // El dialogo se abre desde el link del renglon de abajo, no clickeando el
  // preview: en modo fotograma, un clic ahi para abrir un explorador de
  // archivos seria de lo ultimo que esperarias. Con una imagen propia ya
  // puesta, el clic en el preview si sirve para cambiarla.
  const browseCoverImage = () => {
    pywebview.api.browse_cover_image().then((r) => {
      if (r && r.path) setCoverImage(r.path);
    });
  };
  $("#cover-browse-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    browseCoverImage();
  });
  coverPreview.addEventListener("click", () => {
    // OJO: solo cuando NO hay imagen puesta. Con una imagen, el preview es la
    // superficie para encuadrar (arrastrar/rueda) y el "click" con el que
    // termina cada arrastre abria el explorador de archivos -- para cambiarla
    // esta el link "cambiar imagen" del renglon de abajo.
    if (coverSource === "image" && !coverImagePath) browseCoverImage();
  });
  $("#cover-change-btn").addEventListener("click", (e) => {
    e.stopPropagation();
    browseCoverImage();
  });

  setupCoverImageFraming();
  // "Incluir": cada capa es un interruptor suelto, y las dos pueden quedar
  // apagadas (esa es la portada pelada, sin marco ni grano).
  $("#cover-layer-template").addEventListener("click", () => {
    coverLayers.template = !coverLayers.template;
    renderCoverModal();
    dibujarFotogramaDePortada(); // el fotograma del panel acompana al interruptor
  });
  $("#cover-layer-textures").addEventListener("click", () => {
    coverLayers.textures = !coverLayers.textures;
    renderCoverModal();
    dibujarFotogramaDePortada();
  });
  // "Que guardar": aca SI hay que dejar una prendida -- sin ninguna no habria
  // archivo que guardar.
  $$("#cover-mode-picker .cover-option").forEach((opt) => {
    opt.addEventListener("click", () => {
      const key = opt.dataset.value;
      const otra = key === "full" ? "empty" : "full";
      if (coverWant[key] && !coverWant[otra]) return;
      coverWant[key] = !coverWant[key];
      renderCoverModal();
    });
  });

  $("#cover-modal-save").addEventListener("click", () => {
    const usingImage = coverSource === "image";
    if (usingImage && !coverImagePath) return;
    const isVideo = !!(lastState && lastState.media_is_video);
    // Segundos del ARCHIVO (no del loop): la barra del panel recorre el video
    // entero -- ver source_time en save_cover (api.py).
    const sourceTime = !usingImage && isVideo ? coverVideoEl().currentTime : null;
    const mode = coverWant.full && coverWant.empty ? "both" : (coverWant.empty ? "empty" : "full");
    // El encuadre elegido a mano viaja junto (zoom, x, y) -- ver
    // build_focus_crop en engine.py, que hace el mismo recorte.
    const focus = usingImage
      ? [coverImageFocus.zoom, coverImageFocus.x, coverImageFocus.y]
      : null;
    closeCoverModal();
    saveCoverNow(sourceTime, mode, usingImage ? coverImagePath : null, focus);
  });
  $("#cover-modal-cancel").addEventListener("click", closeCoverModal);
  $("#cover-modal-close").addEventListener("click", closeCoverModal);
  document.addEventListener("click", (e) => {
    if ($("#cover-modal").hidden) return;
    if (e.target.closest("#cover-modal") || e.target.closest("#save-cover-btn")) return;
    closeCoverModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#cover-modal").hidden) closeCoverModal();
  });
});

// ------------------------------------- cerrar/abrir el previsualizador
//
// Deja la ventana con solo el panel de controles (para trabajar al lado de
// otra cosa, o cuando ya no hace falta ver el fotograma). Ademas de esconder
// el panel derecho hay que angostar la VENTANA: si no, quedaba el mismo
// hueco de 1180px con el fondo pelado a la derecha. El tamano lo cambia el
// proceso principal (ver "window-collapse-preview" en main.js) porque el
// renderer no puede tocar su propia ventana.
let previewCollapsed = false;

let previewAnimToken = 0;

function togglePreviewPanel() {
  previewCollapsed = !previewCollapsed;
  const root = $(".app-root");
  root.classList.toggle("preview-collapsed", previewCollapsed);
  // .preview-animating mantiene vivo el previsualizador mientras la ventana
  // se desliza (ver la regla en styles.css): sin esto .preview-collapsed lo
  // apaga en el primer fotograma y del deslizamiento no se ve nada.
  root.classList.add("preview-animating");
  const btn = $("#preview-toggle-btn");
  // Desplegado: el panel de dos zonas de siempre (sidebarToggle) -- probado
  // con un chevron adentro (ver el comentario de sidebarToggle en ICONS) y
  // se veia apretado en un panel tan angosto, asi que se descarto. Colapsado:
  // una flecha doble APARTE (no metida en el icono del panel) apuntando a
  // donde reaparece el previsualizador si se vuelve a tocar.
  btn.innerHTML = iconSvg(previewCollapsed ? "chevronsRight" : "sidebarToggle", 20, 1.2);
  btn.title = previewCollapsed ? "Mostrar el previsualizador" : "Ocultar el previsualizador";
  // El ancho del panel sale del CSS (--panel-width), asi que la medida vive
  // en un solo lugar.
  const panelWidth = parseInt(
    getComputedStyle(document.documentElement).getPropertyValue("--panel-width"), 10
  ) || 365;
  // Clicks repetidos: solo el ULTIMO puede apagar .preview-animating. Sin el
  // token, la promesa de un deslizamiento viejo terminaba despues del nuevo y
  // dejaba el previsualizador apagado a mitad de la animacion en curso.
  const token = ++previewAnimToken;
  window.api.collapsePreviewWindow(previewCollapsed ? panelWidth : null).then(() => {
    if (token !== previewAnimToken) return;
    root.classList.remove("preview-animating");
    positionPreviewOverlays();
  });
  // El recuadro del video cambio de tamano: la barra del loop se reubica.
  // (Durante el deslizamiento la reubica sola el listener de "resize".)
  positionPreviewOverlays();
}

// ------------------------------------------- aviso de "esto se esta generando"
//
// Los dos previews se arman con ffmpeg en hilos aparte de Python: el
// fotograma estatico (request_preview) y el fragmento del loop
// (request_loop_preview, el lento). Con un video de buena calidad y texturas
// encima pueden tardar varios segundos en los que la pantalla no cambiaba en
// nada -- se sentia como que la app se habia trabado. La pastilla nunca
// bloquea el panel: se puede seguir tocando el recorte mientras corre y, de
// hecho, un loop mas corto termina antes.
//
// Un trabajo pisado por otro mas nuevo NO avisa nada (Python compara el
// token y se calla, ver _preview_job/_loop_preview_job en api.py) -- pero el
// mas nuevo si avisa, y cada pedido vuelve a prender su bandera, asi que la
// pastilla se apaga cuando termina el ULTIMO, que es lo que importa.
const previewJobs = { frame: false, loop: false };

function setPreviewJob(kind, running) {
  previewJobs[kind] = running;
  const anyRunning = previewJobs.frame || previewJobs.loop;
  // Sin medio cargado (la zona de "suelta aqui tus archivos") no hay nada
  // que generar ni pastilla que mostrar.
  const hasMedia = !!(lastState && lastState.media_path);
  $("#preview-loading").hidden = !anyRunning || !hasMedia;
  if (!anyRunning) return;
  $("#preview-loading-text").textContent = previewJobs.loop
    ? "Generando el loop... puedes seguir editando"
    : "Generando vista previa...";
  // La pastilla va pegada al recuadro real del medio, igual que el boton de
  // agrandar -- y como aparece de a ratos, hay que reubicarla justo cuando se
  // muestra: positionPreviewOverlays no corre sola en ese momento.
  positionPreviewOverlays();
}

// ---------------------------------------------------------- previsualizador
//
// Estado vacio (dropzone) vs. cargado (fotograma real que empuja Python
// via window.onPreviewReady). schedulePreview() debounca las llamadas a
// Api.request_preview() -- mismo criterio que el "self.after" de Tk en
// la version vieja (_schedule_preview), pero con setTimeout.

// El <img id="preview-image"> ya no se MUESTRA (lo reemplazo el canvas en vivo),
// pero sigue haciendo falta como espejo: hay tres cosas que necesitan un <img>
// de verdad y no un canvas -- el tinte del fondo (background-tint.js pide
// naturalWidth), la ventana aparte de "Agrandar" (lee su src, ver
// pywebview-shim.js) y la portada de una foto (el modal reusa ese src).
//
// Se actualiza con retraso y no en cada cuadro: canvas.toDataURL() de 2560x1440
// cuesta decenas de milisegundos y ninguna de las tres cosas necesita ir al dia
// al instante.
// Le pide a Python los mosaicos de textura ya preparados -- los MISMOS archivos
// que come ffmpeg -- y se los pasa al canvas, para que la textura del preview
// sea pixel a pixel la del video exportado (ver prepared_textures en api.py y
// dibujarTextura en live-preview.js).
//
// Con retraso y en su propio pedido: preparar un mosaico nuevo cuesta trabajo de
// PIL, y solo hace falta cuando cambia la ESCALA de una textura (el resto de las
// veces sale del cache y vuelve al instante). Mientras no llega, el canvas
// dibuja su propia baldosa, que se ve igual de fuerte.
let preparedTexturesTimer = null;
function schedulePreparedTextures(delay = 200) {
  if (preparedTexturesTimer) clearTimeout(preparedTexturesTimer);
  preparedTexturesTimer = setTimeout(() => {
    preparedTexturesTimer = null;
    if (typeof pywebview === "undefined") return;
    pywebview.api.prepared_textures()
      .then((lista) => LivePreview.setPreparedTextures(lista))
      .catch(() => {});
  }, delay);
}

let mirrorTimer = null;
function scheduleMirror(delay = 450) {
  if (mirrorTimer) clearTimeout(mirrorTimer);
  mirrorTimer = setTimeout(() => {
    mirrorTimer = null;
    // Mientras el loop corre, NO: canvas.toDataURL() de 2560x1440 son decenas de
    // milisegundos de hilo principal, ademas de traerse el lienzo de vuelta
    // desde la placa de video, y eso a mitad de reproduccion es un tiron a la
    // vista. Nadie mira el espejo en ese rato (el panel de portada se abre en
    // pausa y con video usa su propio fragmento), asi que se reintenta y se
    // actualiza en cuanto para.
    if (LivePreview.isPlaying()) {
      scheduleMirror(delay);
      return;
    }
    const uri = LivePreview.dataUri();
    if (!uri) return;
    const img = $("#preview-image");
    img.src = uri;
    // El color del fondo NO sale de aca: lo lleva el ambilight, muestreando el
    // canvas seguido y llegando de a poco (ver arrancarAmbilight en
    // live-preview.js). Antes se retintaba en este punto y era justo lo que
    // pegaba el salto: el fondo se clavaba en el color del cuadro que hubiera
    // en el instante del cambio.
    if (window.setHalftoneBackgroundImage) {
      img.decode().then(() => window.setHalftoneBackgroundImage(img)).catch(() => {});
    }
  }, delay);
}

const Preview = (() => {
  let timer = null;

  // Ya no le pide un fotograma a ffmpeg: el previsualizador se compone en vivo
  // en el canvas (ver live-preview.js), asi que un cambio se ve en el cuadro
  // siguiente y NO hay pastilla de "Generando vista previa".
  //
  // Lo unico que sigue viniendo de Python es la GEOMETRIA: la caja donde cae el
  // medio (content_box) la calcula engine.content_box, y controles como el de
  // bordes la cambian. get_state es barato -- no toca ffmpeg -- asi que se le
  // pide de nuevo y con eso el canvas ya sabe donde dibujar. El nombre y la
  // firma se mantienen para no tocar los ~15 lugares que llaman a esto.
  function schedulePreview(delay = 80) {
    LivePreview.redraw();
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      if (typeof pywebview === "undefined") return;
      pywebview.api.get_state().then((s) => {
        if (!s || !s.media_path) return;
        LivePreview.applyState(s);
        schedulePreparedTextures();
        scheduleMirror();
      }).catch(() => {});
    }, delay);
  }

  function applyState(state) {
    const hasMedia = !!state.media_path;
    $("#preview-dropzone").hidden = hasMedia;
    // El canvas en vivo se entera de TODO por aca: medio, texturas, plantilla,
    // recorte y velocidad (ver live-preview.js).
    LivePreview.applyState(state);
    if (hasMedia) {
      schedulePreparedTextures();
      scheduleMirror();
    }
    // El boton de agrandar NO se prende con solo tener el medio cargado: hay
    // un rato (mientras ffmpeg arma el fotograma) en que todavia no hay nada
    // que agrandar, y ahi quedaba flotando en la esquina de la zona vacia --
    // positionPreviewOverlays no lo podia reubicar porque no tenia recuadro
    // que medir, asi que caia en el right/top de respaldo del CSS. Ahora lo
    // prende ella sola, recien cuando hay fotograma o video a la vista.
    if (!hasMedia) {
      $("#preview-expand").hidden = true;
      $("#preview-image").removeAttribute("src");
      hideLoopPreview();
      // Se quito el medio a mitad de un preview: el trabajo que quedo
      // corriendo ya no le importa a nadie y su pastilla tiene que irse ya,
      // sin esperar el aviso de Python.
      setPreviewJob("frame", false);
      setPreviewJob("loop", false);
      // Tambien resetea el matiz que venia siguiendo el ambilight, para que el
      // proximo medio no arranque interpolando desde el color del anterior.
      if (window.ambilightFromSource) window.ambilightFromSource(null);
      if (window.setHalftoneBackgroundImage) window.setHalftoneBackgroundImage(null);
    }
  }

  // Quedo por compatibilidad: ya no se llama a request_preview (el
  // previsualizador se compone en vivo), asi que este evento no deberia llegar.
  // Si llega -- por ejemplo de un pedido que quedo en vuelo al arrancar -- lo
  // unico que hace es apagar la pastilla y NO pisar nada de lo que se ve.
  function onReady(payload) {
    setPreviewJob("frame", false);
    const dataUri = payload && payload.data_uri;
    if (!dataUri) return;
    if (true) return;
    const img = $("#preview-image");
    img.src = dataUri;
    animateImageRefresh(img);
    // Le pasa este fotograma al fondo animado activo -- tintBackgroundFromImage
    // (silk-aurora-background.js, retinta un aurora abstracto) o
    // setHalftoneBackgroundImage (halftone-background.js, trama la imagen
    // real) segun cual este cargado en index.html; el que no este activo
    // queda undefined y no hace nada. setAdaptiveAccent (tambien en
    // silk-aurora-background.js) retinta --accent/--accent-glow en
    // styles.css con el mismo matiz -- foco de inputs, chip de velocidad
    // seleccionado, etc. siguen al mismo color que el fondo. decode()
    // asegura que el bitmap ya este listo para leer pixeles/subir a
    // textura antes de usarlo (el data URI es local asi que resuelve casi
    // al toque, pero sin esperarlo se podia leer basura a medio decodificar).
    if (window.tintBackgroundFromImage || window.setHalftoneBackgroundImage || window.setAdaptiveAccent) {
      img.decode().then(() => {
        if (window.tintBackgroundFromImage) window.tintBackgroundFromImage(img);
        if (window.setHalftoneBackgroundImage) window.setHalftoneBackgroundImage(img);
        if (window.setAdaptiveAccent) window.setAdaptiveAccent(img);
      }).catch(() => {});
    }
    // Si el loop se esta reproduciendo (video visible), este fotograma NO
    // lo interrumpe -- se deja el <img> actualizado por debajo pero oculto.
    // request_preview y request_loop_preview corren en paralelo por el
    // mismo cambio de recorte/velocidad/etc.; si este llegaba primero (o
    // durante la reproduccion) y se forzaba la vuelta al fotograma
    // estatico, se sentia como que el video "se congelaba" a mitad de
    // reproduccion -- el loop en si ya se actualiza solo, en vivo, cuando
    // termine de regenerarse (ver onLoopPreviewReady), sin cortar nada.
    const video = $("#loop-preview-video");
    if (video.hidden) img.hidden = false;
  }

  // Pantalla completa DENTRO de la misma ventana (estilo YouTube), con la
  // Fullscreen API del navegador sobre #preview-surface -- antes esto abria
  // una BrowserWindow de Electron aparte (ver previewWindow.js/main.js),
  // que el usuario no queria (queria quedarse en la misma ventana) y que
  // ademas nunca se habia confirmado con un clic real (erick lo dejo
  // marcado como pendiente de verificar en el README). La Fullscreen API
  // resuelve las dos cosas de una: mismo documento/ventana, y "Esc para
  // salir" viene gratis del propio estandar -- Electron ya lo respeta, no
  // hace falta escuchar la tecla a mano.
  // La ventana se crea con resizable:false (ver createMainWindow en main.js)
  // y en Windows eso le impide agrandarse: la pagina entraba en "pantalla
  // completa" pero la VENTANA se quedaba de 1180x760, asi que el video
  // terminaba metido en una esquina de la pantalla con el escritorio
  // alrededor. Se destraba justo antes de pedirla y se vuelve a trabar al
  // salir. El await es de un ida y vuelta por IPC (milisegundos): la
  // activacion por click sigue valiendo, asi que requestFullscreen no se
  // rechaza por falta de gesto del usuario.
  async function toggleExpand() {
    const surface = $("#preview-surface");
    if (document.fullscreenElement === surface) {
      document.exitFullscreen();
      return;
    }
    try {
      await window.api.setWindowResizable(true);
    } catch (e) {
      // Version vieja del preload sin este metodo -- se intenta igual.
    }
    surface.requestFullscreen().catch(() => {});
  }

  function init() {
    $("#preview-dropzone").addEventListener("click", () => {
      pywebview.api.browse_media().then(handleResult);
    });
    $("#preview-expand").addEventListener("click", toggleExpand);
    // Cada fotograma nuevo puede venir con otra proporcion (cambio el borde,
    // se puso o quito la plantilla): el boton de agrandar se reubica en la
    // esquina del recuadro nuevo.
    $("#preview-image").addEventListener("load", positionPreviewOverlays);
    // Actualiza el icono/titulo del boton tanto al entrar como al salir --
    // cubre el click del boton, la tecla Esc, Y salir por otras vias del
    // sistema (ej. otro atajo de fullscreen), que no pasan por
    // toggleExpand() pero SI disparan este evento.
    document.addEventListener("fullscreenchange", () => {
      const isFull = document.fullscreenElement === $("#preview-surface");
      const btn = $("#preview-expand");
      btn.innerHTML = iconSvg(isFull ? "shrink" : "expand");
      btn.title = isFull ? "Salir de pantalla completa" : "Agrandar";
      // (Volver a trabar el tamano de la ventana lo hace el proceso
      // principal con "leave-html-full-screen" -- ver main.js.)
    });
    // Esc estando agrandado = volver al previsualizador normal, igual que F11.
    // El navegador lo hace solo, pero solo si el foco esta donde el espera: si
    // quedo en un campo de texto o en un desplegable de la app, la tecla se la
    // lleva otro y la unica salida era el boton. Colgado del documento, llega
    // siempre. Si hay un panel abierto encima, el otro listener lo cierra y
    // este saca la pantalla completa -- las dos cosas son lo que uno espera.
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      if (document.fullscreenElement !== $("#preview-surface")) return;
      document.exitFullscreen().catch(() => {});
    });
    // F11 estando agrandado = volver al previsualizador normal. La tecla la
    // atrapa el proceso principal (ver before-input-event en main.js) porque
    // alla es donde el menu por defecto de Electron la usaba para la pantalla
    // completa de la VENTANA, que se desincronizaba con la del documento y
    // dejaba el previsualizador maximizado sin salida. Sin nada agrandado no
    // hace nada, a proposito: entrar necesita un gesto real del usuario (el
    // boton), no vale un aviso por IPC.
    if (window.api && window.api.onExitPreviewFullscreen) {
      window.api.onExitPreviewFullscreen(() => {
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      });
    }
  }

  return { init, applyState, onReady, schedulePreview };
})();

// ------------------------------------------------- fragmento del loop
// Reusa dentro de #preview-surface el mismo hueco del fotograma estatico,
// pero mostrando el video real (FASE 1 de la generacion, ver
// request_loop_preview en api.py) -- SIN la fase 2, lenta, de repetir +
// mezclar con el audio completo, asi que no afecta el tiempo de export.
function buildFileUrl(path) {
  return `file:///${encodeURI(path.replace(/\\/g, "/"))}`;
}

// Ruta del ultimo fragmento que compuso ffmpeg. Ya no se usa para ver el loop
// (eso es el canvas en vivo): queda para el panel de portada, que necesita un
// mp4 real donde buscar un fotograma exacto.
let loopPreviewPath = null;

function hideLoopPreview() {
  LivePreview.pause();
  loopPreviewPath = null;
  $("#loop-preview-play").hidden = true;
  $("#loop-preview-controls").hidden = true;
  stopBeat();
  // El <video> del fragmento ya no se muestra nunca, pero si quedo algo cargado
  // de una version anterior se suelta igual (memoria y decodificador).
  const video = $("#loop-preview-video");
  if (video.hidden) return;
  video.pause();
  video.hidden = true;
  video.removeAttribute("src");
  video.load();
}

// ------------------------------------------------ el beat junto al loop
//
// El mp4 del preview sale sin audio (-an en build_compose_command), asi que
// el beat se reproduce aparte, con un <audio> propio sincronizado al play/
// pausa del loop. El beat corre de largo mientras el video da vueltas -- NO se
// vuelve a empezar en cada vuelta del loop -- igual que en el video final, que
// repite el pedazo de video hasta cubrir el beat entero.
//
// Lo que SI hace es empezar de nuevo cuando se TERMINA (loop en el <audio>, ver
// index.html): el previsualizador da vueltas para siempre, asi que el beat se
// acababa y el resto de la sesion quedaba en silencio -- se veia el loop
// repitiendose sin nada de audio hasta que uno pausaba y volvia a darle play.
//
// Ojo, es el archivo crudo: la exportacion ademas le aplica la normalizacion de
// audio_strategy_args, asi que el volumen puede no ser exactamente el del mp4
// final -- y el volumen de aca abajo es SOLO para escuchar, no viaja al video.
function beatEl() {
  return $("#loop-preview-beat");
}

// Si el beat TIENE que estar sonando ahora mismo: lo prende el play y lo apaga
// la pausa, y nada mas. Es la unica fuente de verdad, y a proposito no se deduce
// de si el canvas esta corriendo: hay huecos en los que el <video> queda pausado
// sin que el usuario haya pausado nada -- el cambio a la copia liviana a mitad
// de reproduccion (medido: casi un segundo con LivePreview.isPlaying() en false)
// y el final del clip antes de dar la vuelta. En esos huecos, mirar el canvas
// dejaba al beat sin nadie que lo volviera a arrancar si justo se detenia ahi.
let beatSonando = false;

// Volumen del beat en el previsualizador. Se guarda para la proxima sesion: sin
// esto la unica forma de bajarlo era bajarle el volumen a la maquina entera,
// que es justo lo que este control viene a sacar del medio.
const BEAT_VOLUME_KEY = "loopPreviewBeatVolume";
const BEAT_MUTED_KEY = "loopPreviewBeatMuted";
let beatVolume = 1;   // 0..1, como lo quiere el <audio>
let beatMuted = false;
// Donde volver al des-silenciar: el parlante silencia sin mover el volumen
// guardado, asi que este es el valor que la barrita vuelve a mostrar.
let beatVolumePrevio = 1;

function loadBeatVolume() {
  try {
    const v = parseFloat(localStorage.getItem(BEAT_VOLUME_KEY));
    if (Number.isFinite(v)) beatVolume = Math.min(1, Math.max(0, v));
    beatMuted = localStorage.getItem(BEAT_MUTED_KEY) === "1";
  } catch (e) {
    // Sin localStorage se arranca al 100% y no se guarda nada -- el control
    // funciona igual, solo que no se acuerda.
  }
  if (beatVolume > 0) beatVolumePrevio = beatVolume;
}

function saveBeatVolume() {
  try {
    localStorage.setItem(BEAT_VOLUME_KEY, String(beatVolume));
    localStorage.setItem(BEAT_MUTED_KEY, beatMuted ? "1" : "0");
  } catch (e) {
    // ver loadBeatVolume
  }
}

// Un solo lugar le escribe el volumen al <audio> y pone al dia el parlante y la
// barrita, asi lo que se ve y lo que se escucha no se pueden separar.
function syncBeatVolumeUi() {
  const beat = beatEl();
  beat.volume = beatVolume;
  // Silencio por muted y no por volumen 0: son dos palancas distintas del
  // <audio>, y separadas la barrita se acuerda de donde estaba.
  beat.muted = beatMuted;
  const sinSonido = beatMuted || beatVolume === 0;
  const boton = $("#loop-preview-mute");
  // Rearmar el SVG solo cuando el icono cambia de verdad: por aca se pasa
  // tambien en cada play (y en cada vuelta del vigilante), y reescribir el
  // innerHTML de al lado del video no es gratis.
  const icono = sinSonido ? "volumeOff" : beatVolume <= 0.5 ? "volumeLow" : "volume";
  if (boton.dataset.icono !== icono) {
    boton.innerHTML = iconSvg(icono);
    boton.dataset.icono = icono;
  }
  boton.title = sinSonido ? "Volver a escuchar el beat" : "Silenciar el beat";
  const barra = $("#loop-preview-vol");
  barra.value = Math.round((sinSonido ? 0 : beatVolume) * 100);
  paintSliderFill(barra);
}

// Arranca el beat (o lo retoma). fromZero solo hace falta para volver a empezar
// uno que YA venia sonando: un beat recien cargado arranca en 0 solo, y despues
// de una pausa se retoma donde quedo -- que es justo lo que se pidio.
function startBeat(fromZero) {
  const beat = beatEl();
  // audio_preview_path: el original si Chromium lo puede tocar tal cual, o
  // una copia en aac si no (ver needs_audio_preview_proxy en engine.py --
  // un wav de 32-bit float, el que exportan por defecto FL Studio/Ableton/
  // Logic, tira NotSupportedError y dejaba el beat mudo sin ningun aviso).
  const path = lastState && (lastState.audio_preview_path || lastState.audio_path);
  if (!path) return; // todavia sin beat cargado -- el loop se ve igual, muteado
  beatSonando = true;
  const url = buildFileUrl(path);
  if (beat.src !== url) {
    beat.src = url;
    beat.currentTime = 0;
  } else if (fromZero) {
    beat.currentTime = 0;
  }
  // El volumen se aplica ANTES del play: si no, el primer instante sale al 100%
  // aunque estuviera bajado o en silencio.
  syncBeatVolumeUi();
  // Puede seguir fallando si la copia todavia no esta lista (se arma en
  // segundo plano al cargar el beat) -- el loop se sigue viendo, solo sin
  // sonido hasta el proximo play/pausa. Se loguea en vez de tragarselo en
  // silencio: antes un fallo real (el wav de 32-bit) no dejaba ningun rastro.
  beat.play().catch((err) => console.error("[beat] no se pudo reproducir:", err && err.name, err && err.message));
}

// Pausa PEDIDA: el beat se queda donde esta (no vuelve a 0) para poder seguir
// desde ahi al quitar la pausa, y baja la bandera para que el vigilante no lo
// vuelva a arrancar por atras.
function pauseBeat() {
  beatSonando = false;
  beatEl().pause();
}

// Para el beat y lo SUELTA: sin src no queda nada que el vigilante pueda volver
// a arrancar. Hace falta porque quitar el beat a mitad de reproduccion dejaba el
// archivo viejo sonando. Esto SI vuelve a 0: es "no hay beat", no una pausa.
function stopBeat() {
  const beat = beatEl();
  beatSonando = false;
  beat.pause();
  beat.currentTime = 0;
  if (beat.getAttribute("src")) {
    beat.removeAttribute("src");
    beat.load();
  }
}

// Sin autoplay: se regenera solo (con el mismo debounce que el fotograma
// estatico) cada vez que cambia el recorte o la velocidad (ver
// LoopSlider.notify() y el listener de #speed-control), pero el usuario
// decide cuando verlo -- #loop-preview-play es la unica forma de
// reproducirlo, nunca se dispara solo.
//
// Y NO se compone al cargar el video: con el recorte entero (3:31, por
// ejemplo) "el loop" es la pelicula completa, o sea un rato largo de ffmpeg
// para un fragmento que no hace loop de nada. Recien arranca cuando el
// pedazo esta DEFINIDO -- o el usuario movio el recorte (notify), o el
// estado ya viene recortado porque se aplico un preset. De ahi en adelante
// cualquier cambio (velocidad, bordes, texturas, plantilla) lo regenera
// igual que antes.
let loopTrimDefined = false;
let lastLoopMediaPath = null;

// ...salvo que el clip sea corto: hasta 3 minutos, componer "el loop entero"
// es un rato razonable de ffmpeg, asi que ahi se genera solo desde el
// arranque y el usuario ya puede darle play sin tocar nada. La espera solo
// aplica a los clips largos, que son los que hacian doler.
const LOOP_AUTO_MAX_DURATION = 180;

// Recorte "de verdad" = no abarca el clip completo. El margen de 0.05s es
// por el redondeo de los m:ss que se pueden escribir a mano.
function stateHasRealTrim(state) {
  if (!state.media_duration) return false;
  const start = state.trim_start ?? 0;
  const end = state.trim_end ?? state.media_duration;
  return start > 0.05 || end < state.media_duration - 0.05;
}

function syncLoopTrimDefined(state) {
  if (state.media_path !== lastLoopMediaPath) {
    // Medio nuevo: vuelve a esperar el recorte. Y el fragmento del anterior
    // ya no aplica -- su boton de play seguia ahi y reproducia el loop del
    // video viejo.
    lastLoopMediaPath = state.media_path;
    loopTrimDefined = false;
    hideLoopPreview();
  }
  // media_duration llega null al principio (el sondeo del clip corre en otro
  // hilo) -- el estado vuelve a pasar por aca cuando ya se sabe.
  if (state.media_duration && state.media_duration <= LOOP_AUTO_MAX_DURATION) {
    loopTrimDefined = true;
  }
  if (stateHasRealTrim(state)) loopTrimDefined = true;
}

// Ya no se compone ningun fragmento para el previsualizador: lo que se ve es el
// canvas en vivo (ver live-preview.js). Estas dos funciones quedan porque las
// llaman los controles de siempre, pero ahora lo unico que hacen es refrescar el
// canvas y ofrecer el boton de "ver el loop" cuando hay un video listo.
//
// El unico que TODAVIA le pide un mp4 a ffmpeg es el panel de portada, que
// necesita buscar un fotograma exacto -- y lo pide por su cuenta (ver
// setupCoverFramePicker), no por aca.
function invalidateLoopPreview() {
  LivePreview.redraw();
  // El boton central aparece en cuanto hay un video que se pueda ver. No hay
  // nada que esperar: apretarlo lo pone a andar en el acto.
  const hayVideo = !!(lastState && lastState.media_is_video && lastState.media_path);
  if (hayVideo && !LivePreview.isPlaying()) $("#loop-preview-play").hidden = false;
  // La barra de pausa/posicion tambien esta disponible desde el arranque: antes
  // aparecia recien despues del primer play, porque hasta entonces no habia
  // ningun fragmento cargado. Ahora el canvas ya muestra el video, asi que es un
  // reproductor desde el primer momento -- que se VEA o no lo sigue decidiendo
  // el cursor encima del previsualizador (ver .loop-preview-controls en
  // styles.css); esto solo la habilita.
  $("#loop-preview-controls").hidden = !hayVideo;
  if (hayVideo) syncLoopToggleIcon();
}

// Se mantiene el nombre porque lo llaman el recorte y la velocidad: con el
// canvas eso ya no cuesta nada, solo hay que redibujar con los valores nuevos
// (la geometria la trae Preview.schedulePreview, que los dos ya llaman).
function scheduleLoopPreview(delay = 0) {
  LivePreview.redraw();
}

// Ya NADIE pide fragmentos del loop: el previsualizador se compone en vivo en el
// canvas y el panel de portada recorre el video entero (ver
// setupCoverFramePicker). Esto queda porque el evento puede llegar de un pedido
// que quedara en vuelo, y lo unico que hace es apagar la pastilla.
window.onLoopPreviewReady = function (payload) {
  setPreviewJob("loop", false);
  if (payload && payload.error) console.error("[loop preview] ffmpeg:", payload.error);
  if (payload && payload.path) loopPreviewPath = payload.path;
};

function playLoopPreview() {
  // Sin composicion de por medio: el canvas ya tiene el medio real cargado y lo
  // dibuja compuesto cuadro a cuadro, asi que "ver el loop" es simplemente
  // ponerlo a andar. Antes esto esperaba un mp4 armado por ffmpeg.
  if (!LivePreview.ready()) return;
  $("#loop-preview-play").hidden = true;
  $("#preview-image").hidden = true;
  $("#loop-preview-controls").hidden = false;
  LivePreview.play();
  positionPreviewOverlays();
  startBeat(false);
  syncLoopToggleIcon();

}

// Controles propios de pausa/reproducir + buscar momento (ver comentario en
// index.html sobre por que no se usan los <video controls> nativos). Se
// atan a los eventos reales del <video> (play/pause/timeupdate) en vez de
// llevar su propio estado, asi nunca se desincronizan del video real.
// La barra del loop y el boton de agrandar van pegados al recuadro REAL del
// medio, no a toda la zona del previsualizador: el video/la imagen van con
// object-fit:contain, asi que su recuadro casi nunca coincide con el hueco
// entero -- la barra salia mas angosta que el video y el boton de agrandar
// flotaba en el vacio, fuera del fotograma. Hay que recalcular en cada
// cambio de tamano (ventana, pantalla completa, cerrar el panel) porque el
// recuadro se mueve con ellos.
function visibleMediaBox() {
  // Lo que se ve es el canvas en vivo; los otros dos quedaron de respaldo.
  const lienzo = LivePreview.element();
  if (lienzo && !lienzo.hidden && LivePreview.ready()) return lienzo.getBoundingClientRect();
  const video = $("#loop-preview-video");
  if (!video.hidden && video.videoWidth) return video.getBoundingClientRect();
  const img = $("#preview-image");
  if (!img.hidden && img.naturalWidth) return img.getBoundingClientRect();
  return null;
}

function positionPreviewOverlays() {
  const box = visibleMediaBox();
  // Sin recuadro que medir no hay donde apoyar el boton de agrandar (y nada
  // que agrandar tampoco): se esconde en vez de quedarse en la esquina de la
  // zona entera, que es lo unico que sabe hacer el CSS solo. Esta es la
  // UNICA linea que lo vuelve a mostrar -- ver applyState.
  $("#preview-expand").hidden = !box;
  if (!box) return;
  const surface = $("#preview-surface").getBoundingClientRect();
  const left = Math.round(box.left - surface.left);
  const right = Math.round(surface.right - box.right);
  const top = Math.round(box.top - surface.top);
  const bottom = Math.round(surface.bottom - box.bottom);

  const controls = $("#loop-preview-controls");
  if (!controls.hidden) {
    controls.style.left = `${left}px`;
    controls.style.right = `${right}px`;
    controls.style.bottom = `${bottom + 12}px`;
  }
  // Dentro de la esquina de arriba a la derecha DEL MEDIO.
  const expand = $("#preview-expand");
  expand.style.right = `${right + 12}px`;
  expand.style.top = `${top + 12}px`;

  // La pastilla de "generando", en la esquina de arriba a la IZQUIERDA del
  // medio: espejo exacto del boton de agrandar. Antes colgaba de la zona
  // entera (left/top de 12px sobre .preview-surface, ver styles.css) y con el
  // medio centrado por object-fit quedaba flotando bastante arriba del
  // fotograma, desalineada del boton que tiene enfrente.
  const loading = $("#preview-loading");
  loading.style.left = `${left + 12}px`;
  loading.style.top = `${top + 12}px`;
}

// El beat puede estar sonando sin que el canvas se mueva: con una foto de medio
// no hay nada que reproducir, pero el play arranca el beat igual. Ahi el boton
// tiene que mostrar pausa (y pausar, ver alternar) en vez de ofrecer un play que
// lo unico que hacia era volver a empezar el beat.
function algoSonando() {
  return LivePreview.isPlaying() || beatSonando;
}

function syncLoopToggleIcon() {
  const toggle = $("#loop-preview-toggle");
  toggle.innerHTML = iconSvgFilled(algoSonando() ? "playerPause" : "playerPlay");
}

function pauseLoopPreview() {
  LivePreview.pause();
  pauseBeat();
  syncLoopToggleIcon();
}

// Duracion de UNA vuelta del loop en segundos REALES (de reloj, ya con la
// velocidad aplicada) -- la misma cuenta que hace la exportacion real para
// saber cuantas veces repetir el pedazo hasta cubrir el beat entero (ver
// FASE 2 en _run_ffmpeg_job, api.py). Con esto la barra/tiempo pueden
// convertir "donde estoy en el beat completo" a "que parte del recorte le
// toca mostrar al loop" sin tocar nada de LivePreview.
function loopUnitDuration() {
  const state = lastState;
  if (!state) return 0;
  const inicio = state.trim_start ?? 0;
  const fin = state.trim_end ?? state.media_duration ?? 0;
  const speedTxt = state.speed || "1x";
  const speed = parseFloat(String(speedTxt).replace("x", "")) || 1;
  const largo = Math.max(0, fin - inicio);
  return speed > 0 ? largo / speed : largo;
}

function initLoopPreviewControls() {
  const toggle = $("#loop-preview-toggle");
  const seek = $("#loop-preview-seek");
  const timeLabel = $("#loop-preview-time");
  let scrubbing = false;

  window.addEventListener("resize", positionPreviewOverlays);
  document.addEventListener("fullscreenchange", positionPreviewOverlays);

  // Play/pausa del canvas en vivo. Antes esto hablaba con el <video> del
  // fragmento compuesto por ffmpeg y se colgaba de sus eventos play/pause; el
  // canvas no tiene eventos propios, asi que el icono y el beat se sincronizan
  // aca mismo, que es el unico lugar desde donde se arranca y se para.
  const alternar = () => {
    if (algoSonando()) pauseLoopPreview();
    else playLoopPreview();
  };
  toggle.addEventListener("click", alternar);
  // Click en el cuadro = play/pausa, como en cualquier reproductor. La barra
  // esta ENCIMA (hermana, no hija), asi que tocar el boton o arrastrar la
  // barrita no llega hasta aca.
  const lienzo = LivePreview.element();
  if (lienzo) lienzo.addEventListener("click", alternar);

  // La barrita se mueve sola mientras corre: el canvas no emite timeupdate, se
  // lee su posicion (0..1 DENTRO del recorte) en cada cuadro de pantalla.
  //
  // Solo cuando se la esta viendo: la barra aparece al pasar el cursor por el
  // previsualizador (ver .loop-preview-controls en styles.css) y el resto del
  // tiempo esta en opacity 0. Escribirle el valor y el --fill sesenta veces por
  // segundo igual obligaba al navegador a recalcular estilo y repintar en cada
  // cuadro -- justo mientras el video pide toda la maquina -- para mover algo
  // que no se ve.
  let cursorEncima = false;
  const surface = $("#preview-surface");
  surface.addEventListener("pointerenter", () => { cursorEncima = true; });
  surface.addEventListener("pointerleave", () => { cursorEncima = false; });

  // null si el beat todavia no cargo metadata (o no hay beat) -- ahi se cae
  // al comportamiento de siempre (posicion DENTRO del recorte, sin tiempo
  // total: sin beat el loop da vueltas para siempre, no hay "total" que
  // mostrar).
  function duracionBeat() {
    const beat = beatEl();
    return Number.isFinite(beat.duration) && beat.duration > 0 ? beat.duration : null;
  }

  // Un solo lugar pinta la barra Y el tiempo, para que nunca se puedan
  // desincronizar entre si.
  function actualizarBarra(tiempoAbsoluto) {
    const dur = duracionBeat();
    if (dur !== null) {
      const t = tiempoAbsoluto ?? beatEl().currentTime;
      seek.value = Math.round(Math.max(0, Math.min(1, t / dur)) * 1000);
      timeLabel.textContent = `${formatDuration(t)} / ${formatDuration(dur)}`;
      timeLabel.hidden = false;
    } else {
      seek.value = Math.round(LivePreview.progress() * 1000);
      timeLabel.hidden = true;
    }
    // El relleno del progreso ya no lo pinta el navegador (la barra dejo de
    // usar la apariencia nativa para sacarle el contorno, ver
    // .loop-preview-seek en styles.css): se pinta aca, como los demas.
    paintSliderFill(seek);
  }

  const seguirBarra = () => {
    // algoSonando() y no solo LivePreview.isPlaying(): con una FOTO de medio
    // no hay video que reproducir, pero el beat igual suena (ver el
    // comentario de algoSonando mas arriba) -- con el chequeo viejo la barra
    // se quedaba clavada en 0 todo ese tiempo.
    if (cursorEncima && !scrubbing && algoSonando()) actualizarBarra();
    requestAnimationFrame(seguirBarra);
  };
  requestAnimationFrame(seguirBarra);

  seek.addEventListener("input", () => {
    scrubbing = true;
    const frac = seek.value / 1000;
    const dur = duracionBeat();
    if (dur !== null) {
      // Arrastrar mueve el BEAT a ese punto del tema completo (igual que
      // cualquier reproductor) y el loop de video se ajusta solo a la parte
      // del recorte que le toca mostrar en ese instante -- ni LivePreview ni
      // su logica de edicion en vivo se tocan, esto solo llama a su
      // seek(frac) de siempre con el frac ya convertido.
      const tiempoAbsoluto = frac * dur;
      beatEl().currentTime = tiempoAbsoluto;
      const unidad = loopUnitDuration();
      const fracLoop = unidad > 0 ? (tiempoAbsoluto % unidad) / unidad : 0;
      LivePreview.seek(fracLoop);
      timeLabel.textContent = `${formatDuration(tiempoAbsoluto)} / ${formatDuration(dur)}`;
      timeLabel.hidden = false;
    } else {
      LivePreview.seek(frac);
    }
    paintSliderFill(seek);
  });
  seek.addEventListener("change", () => {
    scrubbing = false;
  });

  // ------------------------------------------------- volumen del beat
  const beat = beatEl();
  loadBeatVolume();
  syncBeatVolumeUi();

  // Parlante = silenciar / volver a escuchar. Con la barrita en 0 no silencia
  // (ya no se escucha nada): vuelve al ultimo volumen que hubo.
  $("#loop-preview-mute").addEventListener("click", () => {
    if (beatMuted || beatVolume === 0) {
      beatMuted = false;
      if (beatVolume === 0) beatVolume = beatVolumePrevio || 1;
    } else {
      beatMuted = true;
    }
    syncBeatVolumeUi();
    saveBeatVolume();
  });

  // Mover la barrita tambien saca el silencio: subir el volumen de algo mudo y
  // que siguiera mudo no se entiende desde afuera.
  $("#loop-preview-vol").addEventListener("input", (e) => {
    beatVolume = Math.min(1, Math.max(0, Number(e.target.value) / 100));
    if (beatVolume > 0) {
      beatMuted = false;
      beatVolumePrevio = beatVolume;
    }
    syncBeatVolumeUi();
  });
  // Se guarda al soltar y no en cada pixel del arrastre.
  $("#loop-preview-vol").addEventListener("change", saveBeatVolume);

  // El beat tiene que sonar TODO el rato entre el play y la pausa. El <audio> ya
  // lleva loop, pero esto es la red de seguridad para cuando igual se detiene:
  // se termino el archivo sin que saltara el loop, el decodificador se trabo, el
  // beat todavia no estaba cargado cuando se apreto play. Sin esto el
  // previsualizador seguia dando vueltas en silencio hasta que uno pausaba y
  // volvia a darle play.
  //
  // Va contra beatSonando y NO contra LivePreview.isPlaying(): la pausa de
  // verdad baja esa bandera (ver pauseBeat), asi que esto no puede pelearse con
  // el usuario, y a la vez sigue cubriendo los huecos en los que el canvas se ve
  // parado sin que nadie haya pausado.
  //
  // Arranca por startBeat, que es el que sabe si hay beat y cual es: sin beat no
  // hace nada, y si cambio, pone el nuevo.
  //
  // El listener de "pause" lo levanta en el acto; el temporizador es para lo que
  // no avisa con un evento (un play() que quedo colgado, un archivo que tardo).
  beat.addEventListener("pause", () => {
    if (beatSonando) startBeat(false);
  });
  beat.addEventListener("ended", () => {
    if (beatSonando) startBeat(true);
  });
  setInterval(() => {
    if (beatSonando && beat.paused) startBeat(false);
  }, 400);
}

// --------------------------------------------------- modal "Guardar portada"
// Se abre desde #save-cover-btn (solo visible justo despues de exportar con
// exito, ver cover_available en api.py). Si no hay nada que elegir (foto
// suelta sin plantilla: ni momento del loop ni "parte vacia" tienen
// sentido) se salta el modal y guarda directo, igual que antes.

// Ancla el panel al boton que lo abre en vez de centrarlo -- pegado al
// borde DERECHO del boton, desplegado hacia ARRIBA (el boton vive abajo
// del todo, pegado al de "Generar video"). Misma logica que uso la rama de
// erick para su panel "Generar video" antes de revertirla.
function positionNearTrigger(trigger, panel) {
  // 4px: bien pegado al boton, para que se lea como que sale de ahi (con 10
  // quedaba flotando suelto arriba). La animacion lo acompana subiendo desde
  // esa esquina -- ver animateDropdown(fromBelow) y transform-origin en
  // .cover-modal.
  const margin = 4;
  const rect = trigger.getBoundingClientRect();
  const panelRect = panel.getBoundingClientRect();
  let left = rect.right - panelRect.width;
  left = Math.max(margin, Math.min(left, window.innerWidth - panelRect.width - margin));
  let top = rect.top - margin - panelRect.height;
  if (top < margin) top = rect.bottom + margin; // sin lugar arriba -- cae abajo
  panel.style.left = `${Math.round(left)}px`;
  panel.style.top = `${Math.round(top)}px`;
}

// Estado del modal. La portada puede salir de un fotograma del video (o del
// fotograma compuesto, si el medio es una foto) o de una imagen propia que se
// suelta sobre el mini preview -- para cuando ningun momento del loop sirve.
let coverSource = "frame"; // "frame" | "image"
let coverImagePath = null;
// Las dos tarjetas de "Que guardar" se prenden por separado (reemplazan a la
// casilla "Guardar las dos"): full+empty = las dos, y nunca las dos apagadas.
// Arrancan LAS DOS puestas -- guardar las dos versiones es lo normal, y apagar
// la que no se quiera es un clic.
let coverWant = { full: true, empty: true };
// Que capas se ponen encima. Sueltas y validas para las DOS fuentes (fotograma
// del video o imagen propia) -- antes era una sola casilla "Componer con la
// plantilla y las texturas" que ademas solo aparecia con imagen propia. Estas
// dos SI pueden quedar las dos apagadas: eso es la portada pelada.
let coverLayers = { template: true, textures: true };

const COVER_IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".gif", ".avif"];

function isCoverImage(path) {
  return COVER_IMAGE_EXTS.includes(path.slice(path.lastIndexOf(".")).toLowerCase());
}

function saveCoverNow(sourceTime, mode, sourceImage, imageFocus) {
  // Las dos capas van por separado y valen para cualquier fuente (ver
  // save_cover en api.py). loop_time queda en null: el momento viaja como
  // source_time, en segundos del archivo, porque el selector ya no se limita al
  // pedazo recortado.
  pywebview.api.save_cover(null, mode, sourceImage || null,
                           coverLayers.template, coverLayers.textures,
                           imageFocus || null, sourceTime).then((r) => {
    if (!r.ok) $("#status-text").textContent = r.error || "No se pudo guardar la portada.";
  });
}

// Un solo lugar decide que se ve y que no, para cada combinacion de fuente,
// tipo de medio y plantilla -- antes esas decisiones estaban repartidas entre
// openCoverModal y los listeners, y se contradecian.
function renderCoverModal() {
  const isVideo = !!(lastState && lastState.media_is_video);
  const usingImage = coverSource === "image";
  const hayPlantilla = !!(lastState && lastState.template_path);
  const hayTexturas = !!(lastState && lastState.texture_layers && lastState.texture_layers.length);

  // Un solo renglon para la fuente, con tres botones de los que se ve UNO:
  // traer una imagen, volver al fotograma, o cambiar la que ya hay.
  $("#cover-browse-btn").hidden = usingImage;
  $("#cover-back-btn").hidden = !usingImage;
  $("#cover-change-btn").hidden = !(usingImage && coverImagePath);

  // Elegir el momento solo tiene sentido con un video y en modo fotograma; una
  // foto no tiene nada que recorrer.
  $("#cover-frame-seek").hidden = usingImage || !isVideo;
  $("#cover-frame-canvas").hidden = usingImage || !isVideo;
  // Dos cosas se pelean por el hueco: el fotograma elegido y la maqueta del
  // lienzo (que ahora vale tanto con plantilla como sin ella -- el recorte
  // cuadrado se mantiene igual, lo unico que cambia es si la plantilla va
  // encima). El <img> pelado queda para el fotograma ya compuesto de una foto.
  $("#cover-compose-preview").hidden = !(usingImage && coverImagePath);
  $("#cover-image-preview").hidden = usingImage || isVideo;
  if (usingImage && coverImagePath) layoutCoverComposePreview();
  $("#cover-drop-hint").hidden = !usingImage || !!coverImagePath;

  // "Incluir": cada capa se ofrece solo si existe. Sin plantilla ni texturas
  // cargadas el bloque entero se va -- no tiene sentido ofrecer apagar algo
  // que no esta puesto.
  $("#cover-layer-template").hidden = !hayPlantilla;
  $("#cover-layer-textures").hidden = !hayTexturas;
  $("#cover-layers-block").hidden = !hayPlantilla && !hayTexturas;
  $("#cover-layer-template").classList.toggle("cover-option-active", coverLayers.template);
  $("#cover-layer-textures").classList.toggle("cover-option-active", coverLayers.textures);

  // Las dos opciones valen SIEMPRE, con plantilla o sin ella: el "cuadro"
  // existe igual (sin plantilla es el que dejan los bordes negros, centrado
  // -- ver content_box en engine.py).
  $("#cover-mode-picker").hidden = false;
  $$("#cover-mode-picker .cover-option").forEach((b) => {
    b.classList.toggle("cover-option-active", !!coverWant[b.dataset.value]);
  });

  $("#cover-modal-save").disabled = usingImage && !coverImagePath;

}

// Coloca la imagen propia en el MISMO hueco donde la va a poner ffmpeg: la
// ventana de la plantilla, o el cuadro centrado que dejan los bordes (ver
// build_layout en engine.py). Todo sale del estado, en porcentajes del lienzo
// de 1920x1080, asi que la maqueta acompana cualquier plantilla y cualquier
// valor del control de bordes.
// El lienzo lo dice Python (canvas_size en get_state, sale de MAX_WIDTH/
// MAX_HEIGHT en engine.py) -- estaba clavado en 1920x1080 y al pasar el lienzo
// a 2560x1440 la maqueta ubicaba mal la ventana de la plantilla (560/1920 =
// 29% en vez de 560/2560 = 22%). Los valores de respaldo son solo para el caso
// imposible de no tener estado todavia.
function coverCanvas() {
  const [w, h] = (lastState && lastState.canvas_size) || [];
  return { w: w || 1920, h: h || 1080 };
}

// Encuadre de la imagen propia: se arrastra con el mouse y se agranda con la
// rueda, sin perillas. Es el mismo modelo que usa ffmpeg en build_focus_crop:
// un CUADRADO de lado min(ancho,alto)/zoom colocado en (x, y) sobre 0..1, que
// despues se escala para cubrir el hueco. Al guardar viaja tal cual (ver
// save_cover con image_focus), asi que lo que se ve es lo que sale.
let coverImageFocus = { zoom: 1, x: 0.5, y: 0.5 };

// El hueco donde entra el medio, en pixeles del lienzo: la ventana de la
// plantilla, o el cuadro que dejan los bordes (alto completo y el ancho segun
// scale_pct, igual que build_layout en engine.py).
function coverInnerBox() {
  const box = lastState && lastState.template_path ? lastState.template_box : null;
  if (box) return { x: box[0], y: box[1], w: box[2], h: box[3] };
  const lienzo = coverCanvas();
  const scale = (lastState && lastState.scale_pct) || 100;
  const w = Math.min(lienzo.w, (lienzo.h * scale) / 100);
  return { x: (lienzo.w - w) / 2, y: 0, w, h: lienzo.h };
}

function layoutCoverComposePreview() {
  const win = $("#cover-compose-window");
  const img = $("#cover-compose-img");
  const tpl = $("#cover-compose-tpl");
  const hueco = coverInnerBox();
  const lienzo = coverCanvas();
  win.style.left = `${(hueco.x / lienzo.w) * 100}%`;
  win.style.top = `${(hueco.y / lienzo.h) * 100}%`;
  win.style.width = `${(hueco.w / lienzo.w) * 100}%`;
  win.style.height = `${(hueco.h / lienzo.h) * 100}%`;

  // La plantilla encima solo si se va a componer con ella.
  const conPlantilla = !!(lastState && lastState.template_path) && coverLayers.template;
  tpl.hidden = !conPlantilla;
  if (conPlantilla) tpl.src = buildFileUrl(lastState.template_path);

  const W = img.naturalWidth;
  const H = img.naturalHeight;
  const caja = win.getBoundingClientRect();
  if (!W || !H || !caja.width) return;
  // MISMA cuenta que _frame_cover_image en api.py: "cubrir el hueco" es el
  // zoom 1, y de ahi para arriba se acerca y para abajo la imagen se ve
  // completa con bordes. Lo que sobra se pasea con el foco; lo que falta queda
  // centrado (con bordes no hay nada que pasear).
  const cubrir = Math.max(caja.width / W, caja.height / H);
  const k = cubrir * coverImageFocus.zoom;
  const w = W * k;
  const h = H * k;
  img.style.width = `${w}px`;
  img.style.height = `${h}px`;
  img.style.left = `${(caja.width - w) * (w > caja.width ? coverImageFocus.x : 0.5)}px`;
  img.style.top = `${(caja.height - h) * (h > caja.height ? coverImageFocus.y : 0.5)}px`;
}

function setCoverImage(path) {
  coverImagePath = path;
  coverSource = "image";
  coverImageFocus = { zoom: 1, x: 0.5, y: 0.5 }; // imagen nueva, encuadre limpio
  const img = $("#cover-compose-img");
  img.src = buildFileUrl(path);
  // naturalWidth recien existe cuando el archivo esta decodificado.
  if (img.complete) layoutCoverComposePreview();
  else img.addEventListener("load", layoutCoverComposePreview, { once: true });
  renderCoverModal();
}

// Encuadre con el mouse O con el trackpad: arrastrar mueve el recorte, y para
// agrandar sirven la rueda, dos dedos y la PINZA (Chromium manda la pinza del
// trackpad como un wheel con ctrlKey, con desplazamientos mucho mas chicos).
// Los gestos se escuchan en toda la maqueta, no solo en el hueco de la imagen:
// con la plantilla puesta, el hueco es poco mas de la mitad del ancho y quedaba
// medio preview muerto.
function setupCoverImageFraming() {
  const zona = $("#cover-compose-preview");
  const win = $("#cover-compose-window");
  const img = $("#cover-compose-img");
  let arrastre = null;

  zona.addEventListener("pointerdown", (e) => {
    if (!coverImagePath) return;
    e.preventDefault(); // si no, Chromium arranca su arrastre nativo
    const caja = win.getBoundingClientRect();
    const cubrir = Math.max(caja.width / img.naturalWidth, caja.height / img.naturalHeight);
    const k = cubrir * coverImageFocus.zoom;
    // El foco es 0..1 sobre lo que SOBRA de la imagen respecto del hueco
    // (negativo si no sobra nada), asi que un desplazamiento de dx pixeles de
    // pantalla equivale a dx/sobrante de foco. Sin sobrante no hay paseo.
    arrastre = {
      x: e.clientX,
      y: e.clientY,
      fx: coverImageFocus.x,
      fy: coverImageFocus.y,
      sobraX: caja.width - img.naturalWidth * k,
      sobraY: caja.height - img.naturalHeight * k,
    };
    zona.setPointerCapture(e.pointerId);
    zona.classList.add("dragging");
  });
  zona.addEventListener("pointermove", (e) => {
    if (!arrastre) return;
    const dx = e.clientX - arrastre.x;
    const dy = e.clientY - arrastre.y;
    if (arrastre.sobraX < 0) {
      coverImageFocus.x = Math.max(0, Math.min(1, arrastre.fx + dx / arrastre.sobraX));
    }
    if (arrastre.sobraY < 0) {
      coverImageFocus.y = Math.max(0, Math.min(1, arrastre.fy + dy / arrastre.sobraY));
    }
    layoutCoverComposePreview();
  });
  const soltar = () => {
    arrastre = null;
    zona.classList.remove("dragging");
  };
  zona.addEventListener("pointerup", soltar);
  zona.addEventListener("pointercancel", soltar);

  zona.addEventListener("wheel", (e) => {
    if (!coverImagePath) return;
    e.preventDefault();
    // Proporcional al desplazamiento (no un salto fijo): el trackpad dispara
    // muchos eventos chiquitos y con un factor fijo daba brincos. La pinza
    // (ctrlKey) manda deltas mas chicos todavia, asi que pesa mas.
    const paso = e.ctrlKey ? 0.006 : 0.0025;
    const z = coverImageFocus.zoom * Math.exp(-e.deltaY * paso);
    // Por DEBAJO de 1 la imagen deja de cubrir el hueco y se ve completa, con
    // bordes negros -- es la salida cuando el cuadrado corta los pies. 0.25
    // alcanza para que entre entera hasta una foto 4 veces mas alta que ancha.
    coverImageFocus.zoom = Math.max(0.25, Math.min(4, z));
    layoutCoverComposePreview();
  }, { passive: false });

  // Doble clic / doble toque: vuelve al encuadre de fabrica.
  zona.addEventListener("dblclick", () => {
    coverImageFocus = { zoom: 1, x: 0.5, y: 0.5 };
    layoutCoverComposePreview();
  });
}

function closeCoverModal() {
  const modal = $("#cover-modal");
  if (modal.hidden) return;
  // fromBelow tambien al cerrar, para que se vaya por donde vino (hacia el
  // boton) y no hacia arriba. Pone [hidden] solo al terminar la animacion.
  animateDropdown(modal, false, true);
  // El <video> del panel se queda cargado (volver a abrirlo es instantaneo)
  // pero quieto. No hay nada que devolver al previsualizador: nunca se lo toco.
  if (coverVideo) coverVideo.pause();
}

// El panel tiene su PROPIO <video>, aparte del previsualizador. Es la unica
// forma de que buscar el fotograma de la portada no arrastre lo que se esta
// viendo: si los dos comparten el mismo elemento, mover la barra del panel mueve
// el previsualizador. Lee el mismo archivo (la copia liviana, si hay) y compone
// con las MISMAS capas y la misma geometria, pidiendoselo a LivePreview.
//
// Antes esto era un <video> con un fragmento que armaba ffmpeg en el momento
// ("Cargando vista previa del loop..."), lo que ademas ataba la portada al
// pedazo recortado: no habia forma de sacarla de otra parte del video.
let coverVideo = null;
let coverVideoUrl = null;

function coverVideoEl() {
  if (coverVideo) return coverVideo;
  coverVideo = document.createElement("video");
  coverVideo.id = "cover-source-video";
  coverVideo.muted = true;
  coverVideo.playsInline = true;
  coverVideo.preload = "auto";
  // Fuera de la vista pero en el DOM: un <video> suelto no siempre decodifica.
  coverVideo.style.cssText =
    "position:absolute;width:1px;height:1px;opacity:0;pointer-events:none";
  coverVideo.setAttribute("aria-hidden", "true");
  document.body.appendChild(coverVideo);
  // Se dibuja cuando el fotograma buscado ESTA, no cuando se pidio.
  coverVideo.addEventListener("seeked", dibujarFotogramaDePortada);
  coverVideo.addEventListener("loadeddata", dibujarFotogramaDePortada);
  return coverVideo;
}

function dibujarFotogramaDePortada() {
  const lienzo = $("#cover-frame-canvas");
  if (!lienzo || !coverVideo) return;
  // Con las capas que digan los interruptores de "Incluir": el panel tiene que
  // mostrar lo que se va a guardar. Antes mostraba siempre todo puesto (el
  // fragmento venia compuesto por ffmpeg), asi que apagar "Plantilla" o
  // "Textura" no cambiaba nada a la vista y parecia que el boton no hacia nada.
  LivePreview.composeInto(lienzo, coverVideo, {
    plantilla: coverLayers.template,
    texturas: coverLayers.textures,
  });
}

function setupCoverFramePicker() {
  const seek = $("#cover-frame-seek");
  const v = coverVideoEl();
  const url = LivePreview.sourceUrl();
  if (url && url !== coverVideoUrl) {
    coverVideoUrl = url;
    v.src = url;
  }
  // Arranca en el momento que ya se estaba viendo, que es el candidato natural
  // a portada: uno suele parar el loop justo en el cuadro que le gusta.
  const arranque = LivePreview.currentTime();
  const posicionar = () => {
    const largo = v.duration || 0;
    seek.disabled = !largo;
    seek.value = largo ? Math.round((arranque / largo) * 1000) : 0;
    paintSliderFill(seek);
    v.currentTime = arranque; // el "seeked" dibuja
  };
  if (v.readyState >= 1) posicionar();
  else v.addEventListener("loadedmetadata", posicionar, { once: true });
  renderCoverModal();
}

function openCoverModal() {
  const modal = $("#cover-modal");
  if (!modal.hidden) return; // ya esta abierto (el mouse volvio a entrar)
  const isVideo = !!(lastState && lastState.media_is_video);

  // Estado de arranque en cada apertura: fotograma, sin imagen propia, y las dos
  // versiones de la portada. (Antes, una foto sin plantilla se guardaba directo
  // sin abrir nada; ahora el panel siempre se abre, porque incluso en ese caso
  // sirve para traer una imagen propia.)
  coverSource = "frame";
  coverImagePath = null;
  coverWant = { full: true, empty: true };
  // Las dos capas arrancan puestas: es lo que se veia en el previsualizador,
  // asi que es lo que uno espera de la portada.
  coverLayers = { template: true, textures: true };
  $("#cover-drop-hint").textContent = "Suelta o pega tu imagen";
  $("#cover-image-preview").removeAttribute("src");
  // Con una foto (no video) el "fotograma" es el mismo que ya se ve compuesto
  // en el previsualizador grande -- se reusa su data URI en vez de pedirle a
  // ffmpeg otro igual.
  if (!isVideo) $("#cover-image-preview").src = $("#preview-image").src;
  renderCoverModal();

  // El previsualizador (y el beat) siguen sonando mientras se arma la
  // portada -- pedido del usuario, que se abre este panel de pasada
  // (aparece con solo pasar el cursor) y no quiere que el loop se corte
  // por eso. Es seguro: el panel tiene su PROPIO <video> (ver
  // coverVideoEl), asi que buscar un momento aca no mueve ni pausa el
  // previsualizador grande.
  modal.hidden = false;
  positionNearTrigger($("#save-cover-btn"), modal);
  animateDropdown(modal, true, true); // entra subiendo, desde el boton
  if (isVideo) setupCoverFramePicker();
}

// ---- abre al pasar el cursor, no al hacer click ----
// Pequeno retraso (hoverIntent) para no disparar request_loop_preview() en
// cada pasada accidental del mouse; se cancela si el cursor sigue de largo
// antes de que venza. Una vez abierto, se queda mientras el cursor este
// sobre el boton O sobre el panel -- sin esto, cruzar el hueco entre
// ambos (positionNearTrigger los separa un margen) lo cerraria a mitad de
// camino. El click sigue funcionando como atajo instantaneo (teclado/mouse
// de precision), sin esperar el retraso.
let coverModalHoverTimer = null;
let coverModalCloseTimer = null;

function cancelCoverModalTimers() {
  if (coverModalHoverTimer) {
    clearTimeout(coverModalHoverTimer);
    coverModalHoverTimer = null;
  }
  if (coverModalCloseTimer) {
    clearTimeout(coverModalCloseTimer);
    coverModalCloseTimer = null;
  }
}

function scheduleCoverModalOpen() {
  if (coverModalCloseTimer) {
    clearTimeout(coverModalCloseTimer);
    coverModalCloseTimer = null;
  }
  if (!$("#cover-modal").hidden || coverModalHoverTimer) return;
  coverModalHoverTimer = setTimeout(() => {
    coverModalHoverTimer = null;
    openCoverModal();
  }, 150);
}

// (El cierre automatico al sacar el cursor se quito -- ver el comentario en
// los listeners de #save-cover-btn.)

window.onPreviewReady = Preview.onReady;

// ------------------------------------------------------- ajustar imagen
//
// Reimplementacion en canvas del ImageFocusPicker de Tk (app.py): el
// recuadro que se ve aca es exactamente lo que recorta ffmpeg. La geometria
// vive en JS; Python solo guarda el resultado (set_crop).
//
// El recorte se guarda como RECTANGULO en fracciones 0..1 de la foto
// original (rx, ry, rw, rh). Antes era zoom + punto de foco, que solo sabia
// describir cuadrados; el corte Vertical toma la proporcion de la foto, que
// puede ser cualquiera. Los dos cortes son el MISMO rectangulo con distintas
// reglas de arrastre:
//   cuadrado -- se mueve y se agranda, pero se mantiene cuadrado
//   vertical -- la foto entera; no hay nada que arrastrar
//
// Ojo con una trampa de las fracciones: 0.5 x 0.5 NO es un cuadrado salvo
// que la foto lo sea. Por eso todo el arrastre se hace en PIXELES DEL CANVAS
// (donde la foto se dibuja a escala uniforme, asi que un cuadrado se ve
// cuadrado) y recien al guardar se pasa a fracciones.

const FocusPicker = (() => {
  const W = 352, H = 200; // debe coincidir con FOCUS_PICKER_W/H en api.py
  const HANDLE = 5;       // medio lado del tirador de esquina (dibujo)
  const HIT = 11;         // radio de agarre de una esquina (interaccion)
  let canvas, ctx;
  let image = null;
  let imgW = W, imgH = H; // tamano ajustado (fit) de la imagen dentro del canvas
  let mode = "cuadrado";
  // Recorte en fracciones de la foto original. La foto entera por defecto:
  // es lo que corresponde hasta que Python mande el cuadrado centrado.
  let rx = 0, ry = 0, rw = 1, rh = 1;
  let lastPreviewUri = null;
  // null | {mode:"move"} | {mode:"resize", ax, ay} (ancla = esquina opuesta)
  let drag = null;
  let cropModeOpen = false;

  function clamp01(v) {
    return Math.max(0, Math.min(1, v));
  }

  function minDim() {
    return Math.min(imgW, imgH);
  }

  function origin() {
    return [(W - imgW) / 2, (H - imgH) / 2];
  }

  // El recuadro en pixeles del canvas: [x0, y0, x1, y1].
  //
  // "Ajustar imagen" y "Bordes de la imagen" son controles independientes:
  // el borde solo afecta como se compone el recorte YA HECHO sobre el lienzo
  // final (letterbox en los lados), no que parte de la foto se puede
  // seleccionar aca -- por eso este widget no lo toma en cuenta para nada.
  function finalRect() {
    const [ox, oy] = origin();
    const x = ox + rx * imgW;
    const y = oy + ry * imgH;
    return [x, y, x + rw * imgW, y + rh * imgH];
  }

  // Guarda un recuadro dado en pixeles del canvas, recortandolo para que no
  // se salga de la foto.
  function setRectPx(x0, y0, x1, y1) {
    const [ox, oy] = origin();
    const left = Math.max(ox, Math.min(x0, x1));
    const top = Math.max(oy, Math.min(y0, y1));
    const right = Math.min(ox + imgW, Math.max(x0, x1));
    const bottom = Math.min(oy + imgH, Math.max(y0, y1));
    rx = clamp01((left - ox) / imgW);
    ry = clamp01((top - oy) / imgH);
    rw = clamp01((right - left) / imgW);
    rh = clamp01((bottom - top) / imgH);
  }

  // Esquinas del recuadro visible, con el cursor de flechas que le toca a
  // cada una (nwse = ↘↖, nesw = ↙↗) y su esquina opuesta (el ancla al
  // redimensionar).
  function corners() {
    const [x0, y0, x1, y1] = finalRect();
    return [
      { x: x0, y: y0, cursor: "nwse-resize", ax: x1, ay: y1 },
      { x: x1, y: y0, cursor: "nesw-resize", ax: x0, ay: y1 },
      { x: x0, y: y1, cursor: "nesw-resize", ax: x1, ay: y0 },
      { x: x1, y: y1, cursor: "nwse-resize", ax: x0, ay: y0 },
    ];
  }

  function redraw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, W, H);
    if (!image) {
      ctx.fillStyle = "#8b8b92";
      ctx.font = "12px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText("Carga una imagen", W / 2, H / 2);
      return;
    }
    const [ox, oy] = origin();
    ctx.drawImage(image, ox, oy, imgW, imgH);
    const [cx0, cy0, cx1, cy1] = finalRect();
    if (cx0 > ox + 0.5 || cy0 > oy + 0.5 || cx1 < ox + imgW - 0.5 || cy1 < oy + imgH - 0.5) {
      ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
      ctx.fillRect(ox, oy, imgW, cy0 - oy);
      ctx.fillRect(ox, cy1, imgW, oy + imgH - cy1);
      ctx.fillRect(ox, cy0, cx0 - ox, cy1 - cy0);
      ctx.fillRect(cx1, cy0, ox + imgW - cx1, cy1 - cy0);
    }
    // Blanco en vez del morado de acento -- el fondo ahora toma el color
    // de la foto/video cargado (ver silk-aurora-background.js), asi que un
    // morado fijo terminaba chocando con imagenes de otros colores.
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.strokeRect(cx0, cy0, cx1 - cx0, cy1 - cy0);
    // Tiradores de esquina (cuadraditos) para redimensionar -- en Vertical
    // no van: el recuadro ES la foto entera y no hay nada que ajustar, asi
    // que dibujarlos invitaria a arrastrar algo que no se mueve.
    if (mode === "vertical") return;
    for (const c of corners()) {
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(c.x - HANDLE, c.y - HANDLE, HANDLE * 2, HANDLE * 2);
      ctx.strokeStyle = "rgba(0,0,0,0.35)";
      ctx.lineWidth = 1;
      ctx.strokeRect(c.x - HANDLE + 0.5, c.y - HANDLE + 0.5, HANDLE * 2 - 1, HANDLE * 2 - 1);
    }
  }

  function notify() {
    pywebview.api.set_crop(mode, rx, ry, rw, rh);
    Preview.schedulePreview();
  }


  function canvasPos(e) {
    const rect = canvas.getBoundingClientRect();
    return [e.clientX - rect.left, e.clientY - rect.top];
  }

  function hitTest(px, py) {
    // En Vertical no se agarra nada: el recorte es la foto completa.
    if (mode === "vertical") return null;
    for (const c of corners()) {
      if (Math.abs(px - c.x) <= HIT && Math.abs(py - c.y) <= HIT) {
        return { mode: "resize", ...c };
      }
    }
    // Cualquier otro click en el canvas inicia un arrastre para mover --
    // OJO: antes esto exigia caer dentro de finalRect() (el recuadro
    // VISIBLE, ya encogido por Bordes de la imagen). Con una escala
    // distinta de 100% ese recuadro puede quedar mucho mas angosto que
    // el canvas entero, y limitar el agarre a esa franja hacia casi
    // imposible arrastrar hasta las orillas (el bug que reporto el
    // usuario). El widget de Tk original tampoco exigia esto -- cualquier
    // click con imagen cargada empezaba el arrastre.
    return { mode: "move" };
  }

  // Redimensionar desde una esquina, con la opuesta (ax, ay) clavada. Solo
  // pasa en Cuadrado: un solo lado, el del eje que mas se movio. Tope en el
  // cuadrado mas grande que entra en la foto (minDim) y piso en un tercio
  // -- lo que antes eran los topes de zoom 100..300.
  function applyResize(px, py, ax, ay) {
    let side = Math.max(Math.abs(px - ax), Math.abs(py - ay));
    side = Math.max(minDim() / 3, Math.min(minDim(), side));
    setRectPx(ax, ay, px >= ax ? ax + side : ax - side, py >= ay ? ay + side : ay - side);
    redraw();
  }

  function loadImage(dataUri) {
    if (dataUri === lastPreviewUri) return;
    lastPreviewUri = dataUri;
    if (!dataUri) {
      image = null;
      redraw();
      return;
    }
    const img = new Image();
    img.onload = () => {
      let dispW = W, dispH = Math.round((W * img.naturalHeight) / img.naturalWidth);
      if (dispH > H) {
        dispH = H;
        dispW = Math.round((H * img.naturalWidth) / img.naturalHeight);
      }
      image = img;
      imgW = dispW;
      imgH = dispH;
      redraw();
    };
    img.src = dataUri;
  }

  // El boton es solo un icono, asi que el corte puesto se lee en dos lados:
  // el tooltip del boton y la ayuda de abajo (que ademas cambia porque en
  // Vertical no hay nada que arrastrar y decir "arrastra para ajustar" seria
  // mentir).
  const NOMBRES = { cuadrado: "Cuadrado", vertical: "Vertical" };
  const AYUDAS = {
    cuadrado: "Arrastra para mover · las esquinas para acercar",
    vertical: "Se usa la foto completa, a todo su alto",
  };

  function renderMode() {
    const nombre = NOMBRES[mode] || NOMBRES.cuadrado;
    $("#crop-mode-trigger").title = `Forma del recorte: ${nombre}`;
    $("#focus-hint").textContent = AYUDAS[mode] || AYUDAS.cuadrado;
    $$("#crop-mode-list .preset-dropdown-item").forEach((item) => {
      item.classList.toggle("active", item.dataset.value === mode);
    });
    if (canvas) canvas.style.cursor = mode === "vertical" ? "default" : "grab";
  }

  function applyState(state) {
    mode = state.crop_mode || "cuadrado";
    const r = state.crop_rect || [0, 0, 1, 1];
    rx = clamp01(r[0]); ry = clamp01(r[1]);
    rw = clamp01(r[2]); rh = clamp01(r[3]);
    loadImage(state.show_focus ? state.media_focus_preview : null);
    renderMode();
    redraw();
  }

  function init() {
    canvas = document.getElementById("focus-picker");
    canvas.width = W;
    canvas.height = H;
    ctx = canvas.getContext("2d");

    canvas.addEventListener("pointerdown", (e) => {
      if (!image) return;
      const [px, py] = canvasPos(e);
      const hit = hitTest(px, py);
      if (!hit) return;
      // Mismo motivo que en LoopSlider: si no, el arrastre del recuadro
      // termina convertido en un drag nativo de Chromium.
      e.preventDefault();
      drag = hit.mode === "resize" ? { mode: "resize", ax: hit.ax, ay: hit.ay } : { mode: "move" };
      canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener("pointermove", (e) => {
      if (!drag) {
        // sin arrastre: solo actualizar el cursor (flechas en esquinas,
        // "mover" dentro del recuadro)
        if (!image) return;
        const [px, py] = canvasPos(e);
        const hit = hitTest(px, py);
        canvas.style.cursor = hit ? (hit.mode === "resize" ? hit.cursor : "move") : "default";
        return;
      }
      if (drag.mode === "resize") {
        const [px, py] = canvasPos(e);
        applyResize(px, py, drag.ax, drag.ay);
        return;
      }
      // Movimiento RELATIVO (movementX/Y) en vez de una posicion absoluta
      // (offsetX/Y): offsetX/Y se vuelve poco confiable en cuanto el
      // cursor sale del canvas (mide solo 352x200) -- el delta relativo
      // no depende de estar "dentro". setPointerCapture mantiene el
      // arrastre vivo fuera del canvas.
      //
      // El recuadro se corre entero y se frena contra los bordes de la foto,
      // sin cambiar de tamano (de ahi que se sume el mismo delta a las dos
      // esquinas, y que el clamp sea sobre la posicion y no sobre el lado).
      const [x0, y0, x1, y1] = finalRect();
      const [ox, oy] = origin();
      const dx = Math.max(ox - x0, Math.min(ox + imgW - x1, e.movementX));
      const dy = Math.max(oy - y0, Math.min(oy + imgH - y1, e.movementY));
      setRectPx(x0 + dx, y0 + dy, x1 + dx, y1 + dy);
      redraw();
    });
    const endDrag = () => {
      if (!drag) return;
      drag = null;
      notify();
    };
    canvas.addEventListener("pointerup", endDrag);
    canvas.addEventListener("pointercancel", endDrag);

    $("#focus-reset").addEventListener("click", () => {
      pywebview.api.reset_focus().then((r) => {
        applyState(r.state);
        Preview.schedulePreview();
      });
    });

    // Desplegable del corte -- mismo componente y mismo cableado que el de
    // Estilo de la textura (ver toggleTextureBlendDropdown): la lista se muda
    // a <body> al abrirse, por eso el click de afuera chequea los dos
    // closest() por separado.
    $("#crop-mode-trigger").addEventListener("click", (e) => {
      e.stopPropagation();
      cropModeOpen = !cropModeOpen;
      const list = $("#crop-mode-list");
      $("#crop-mode-dropdown").classList.toggle("open", cropModeOpen);
      if (cropModeOpen) {
        list.hidden = false;
        positionFloatingDropdown($("#crop-mode-trigger"), list);
      }
      animateDropdown(list, cropModeOpen);
    });
    document.addEventListener("click", (e) => {
      if (cropModeOpen && !e.target.closest("#crop-mode-dropdown") && !e.target.closest("#crop-mode-list")) {
        closeCropModeDropdown();
      }
    });
    $$("#crop-mode-list .preset-dropdown-item").forEach((item) => {
      item.addEventListener("click", () => {
        closeCropModeDropdown();
        // El recuadro de arranque de cada corte lo decide Python
        // (_centered_rect): el cuadrado centrado depende de la proporcion de
        // la foto, que es un dato que vive alla.
        pywebview.api.set_crop_mode(item.dataset.value).then((r) => {
          applyState(r.state);
          Preview.schedulePreview();
        });
      });
    });
  }

  function closeCropModeDropdown() {
    if (!cropModeOpen) return;
    cropModeOpen = false;
    $("#crop-mode-dropdown").classList.remove("open");
    animateDropdown($("#crop-mode-list"), false);
  }

  return { init, applyState };
})();

// --------------------------------------------------------- recorte del loop
//
// Slider de doble asa: reimplementa RangeSlider (app.py) en HTML/CSS/JS.
// A diferencia del canvas de arriba, aca SI conviene usar coordenadas
// absolutas (getBoundingClientRect + clientX) en vez de movimiento
// relativo, porque el gesto real es "click en cualquier punto del riel
// mueve la asa mas cercana ahi", no "arrastrar para desplazar" -- y
// clientX no depende de que el cursor siga dentro del elemento.

const LoopSlider = (() => {
  const PAD = 8;
  const MIN_GAP = 0.5;
  let container, fill, handleStart, handleEnd, startEntry, endEntry;
  let duration = 1.0, start = 0.0, end = 1.0;
  let grabbed = null;

  function secToPct(sec) {
    return duration > 0 ? Math.max(0, Math.min(100, (sec / duration) * 100)) : 0;
  }

  function redraw() {
    const startPct = secToPct(start);
    const endPct = secToPct(end);
    handleStart.style.left = `${startPct}%`;
    handleEnd.style.left = `${endPct}%`;
    fill.style.left = `${startPct}%`;
    fill.style.right = `${100 - endPct}%`;
  }

  function updateReadout() {
    $("#trim-readout").textContent = `${formatDuration(end - start)} de loop`;
    // No pisar el campo mientras el usuario esta escribiendo en el (si no,
    // cada digito quedaria reformateado a mitad de tipeo).
    if (document.activeElement !== startEntry) startEntry.value = formatDuration(start);
    if (document.activeElement !== endEntry) endEntry.value = formatDuration(end);
  }

  function xToSec(clientX) {
    const rect = container.getBoundingClientRect();
    const usable = Math.max(1, rect.width - 2 * PAD);
    const frac = (clientX - rect.left - PAD) / usable;
    return Math.max(0, Math.min(1, frac)) * duration;
  }

  function applyDrag(sec) {
    if (grabbed === "start") {
      start = Math.max(0, Math.min(sec, end - MIN_GAP));
    } else {
      end = Math.min(duration, Math.max(sec, start + MIN_GAP));
    }
    redraw();
    updateReadout();
  }

  function notify() {
    pywebview.api.set_trim(start, end);
    // Mover el recorte es LA senal de que ya hay un pedazo de loop que
    // vale la pena componer -- ver scheduleLoopPreview.
    loopTrimDefined = true;
    Preview.schedulePreview();
    scheduleLoopPreview();
  }

  function commitStartEntry() {
    const sec = parseDuration(startEntry.value);
    if (sec !== null) start = Math.max(0, Math.min(sec, end - MIN_GAP));
    redraw();
    updateReadout();
    notify();
  }

  function commitEndEntry() {
    const sec = parseDuration(endEntry.value);
    if (sec !== null) end = Math.min(duration, Math.max(sec, start + MIN_GAP));
    redraw();
    updateReadout();
    notify();
  }

  function applyState(state) {
    if (!state.show_trim || !state.media_duration) return;
    duration = Math.max(MIN_GAP, state.media_duration || 1.0);
    start = state.trim_start ?? 0;
    end = state.trim_end ?? duration;
    redraw();
    updateReadout();
  }

  function init() {
    container = $("#loop-slider");
    fill = container.querySelector(".range-fill");
    handleStart = document.getElementById("loop-handle-start");
    handleEnd = document.getElementById("loop-handle-end");
    startEntry = $("#trim-start-entry");
    endEntry = $("#trim-end-entry");

    startEntry.addEventListener("change", commitStartEntry);
    startEntry.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commitStartEntry();
    });
    endEntry.addEventListener("change", commitEndEntry);
    endEntry.addEventListener("keydown", (e) => {
      if (e.key === "Enter") commitEndEntry();
    });

    container.addEventListener("pointerdown", (e) => {
      // Sin este preventDefault, Chromium sigue con su gesto por defecto:
      // arranca una seleccion de texto y, en cuanto hay algo seleccionado
      // bajo el cursor, la convierte en un ARRASTRE nativo. Eso se robaba el
      // gesto a mitad de camino (la asa dejaba de seguir al mouse, el cursor
      // pasaba al de drag&drop) y encima encendia los dragover/drop del body,
      // que son para archivos de afuera -- ver drop-handler.js.
      e.preventDefault();
      const sec = xToSec(e.clientX);
      grabbed = Math.abs(sec - start) <= Math.abs(sec - end) ? "start" : "end";
      container.setPointerCapture(e.pointerId);
      applyDrag(sec);
    });
    container.addEventListener("pointermove", (e) => {
      if (!grabbed) return;
      applyDrag(xToSec(e.clientX));
    });
    const endDrag = () => {
      if (!grabbed) return;
      grabbed = null;
      notify();
    };
    container.addEventListener("pointerup", endDrag);
    container.addEventListener("pointercancel", endDrag);
  }

  return { init, applyState };
})();
