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
  music: '<path d="M3 17a3 3 0 1 0 6 0a3 3 0 0 0 -6 0" /><path d="M13 17a3 3 0 1 0 6 0a3 3 0 0 0 -6 0" />'
    + '<path d="M9 17v-13h10v13" /><path d="M9 8h10" />',
  expand: '<path d="M16 4h4v4" /><path d="M20 4l-5 5" /><path d="M8 20h-4v-4" /><path d="M4 20l5 -5" />'
    + '<path d="M16 20h4v-4" /><path d="M20 20l-5 -5" /><path d="M8 4h-4v4" /><path d="M4 4l5 5" />',
  shrink: '<path d="M5 9l4 0l0 -4" /><path d="M3 3l6 6" /><path d="M5 15l4 0l0 4" /><path d="M3 21l6 -6" />'
    + '<path d="M19 9l-4 0l0 -4" /><path d="M21 3l-6 6" /><path d="M19 15l-4 0l0 4" /><path d="M21 21l-6 -6" />',
  chevronDown: '<path d="M6 9l6 6l6 -6" />',
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
};

function iconSvg(name, size = 16, strokeWidth = 1) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" `
    + `stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ""}</svg>`;
}

// Iconos "filled" de Tabler (fill=currentColor, sin stroke) -- juego
// aparte de ICONS/iconSvg porque esos son estilo "outline" (sin relleno).
// Se usan en los chips de "Contenido del video" -- iconos de tipo de
// archivo genericos, no la miniatura real (verla ahi, en "Ajustar imagen"
// Y en la galeria de Plantilla/Textura era la misma foto 3 veces).
const ICONS_FILLED = {
  fileDescription: '<path d="M12 2l.117 .007a1 1 0 0 1 .876 .876l.007 .117v4l.005 .15a2 2 0 0 0 1.838 1.844l.157 .006h4l.117 .007a1 1 0 0 1 .876 .876l.007 .117v9a3 3 0 0 1 -2.824 2.995l-.176 .005h-10a3 3 0 0 1 -2.995 -2.824l-.005 -.176v-14a3 3 0 0 1 2.824 -2.995l.176 -.005zm3 14h-6a1 1 0 0 0 0 2h6a1 1 0 0 0 0 -2m0 -4h-6a1 1 0 0 0 0 2h6a1 1 0 0 0 0 -2" />'
    + '<path d="M19 7h-4l-.001 -4.001z" />',
  fileMusic: '<path d="M12 2l.117 .007a1 1 0 0 1 .876 .876l.007 .117v4l.005 .15a2 2 0 0 0 1.838 1.844l.157 .006h4l.117 .007a1 1 0 0 1 .876 .876l.007 .117v9a3 3 0 0 1 -2.824 2.995l-.176 .005h-10a3 3 0 0 1 -2.995 -2.824l-.005 -.176v-14a3 3 0 0 1 2.824 -2.995l.176 -.005zm.447 9.106a1 1 0 0 0 -1.447 .894v3a2 2 0 0 0 -1.995 1.85l-.005 .15a2 2 0 1 0 4 0v-3.382l.553 .276a1 1 0 0 0 .894 -1.788z" />'
    + '<path d="M19 7h-4l-.001 -4.001z" />',
  playerPlay: '<path d="M6 4v16a1 1 0 0 0 1.524 .852l13 -8a1 1 0 0 0 0 -1.704l-13 -8a1 1 0 0 0 -1.524 .852z" />',
  playerPause: '<path d="M9 4h-2a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h2a2 2 0 0 0 2 -2v-12a2 2 0 0 0 -2 -2z" />'
    + '<path d="M17 4h-2a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h2a2 2 0 0 0 2 -2v-12a2 2 0 0 0 -2 -2z" />',
  check: '<path d="M20.707 6.293a1 1 0 0 1 0 1.414l-10 10a1 1 0 0 1 -1.414 0l-5 -5a1 1 0 0 1 1.414 -1.414l4.293 4.293l9.293 -9.293a1 1 0 0 1 1.414 0" />',
  close: '<path d="M6.707 5.293l5.293 5.292l5.293 -5.292a1 1 0 0 1 1.414 1.414l-5.292 5.293l5.292 5.293a1 1 0 0 1 -1.414 1.414l-5.293 -5.292l-5.293 5.292a1 1 0 1 1 -1.414 -1.414l5.292 -5.293l-5.292 -5.293a1 1 0 0 1 1.414 -1.414" />',
};

function iconSvgFilled(name, size = 16) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="currentColor">${ICONS_FILLED[name] || ""}</svg>`;
}

// Botones/elementos estaticos del HTML que se quedan sin icono en el
// marcado (para no repetir el SVG en dos lugares) -- se rellenan una vez
// al arrancar.
function fillStaticIcons() {
  $("#media-chip").querySelector(".btn-icon").innerHTML = iconSvg("trash");
  $("#audio-chip").querySelector(".btn-icon").innerHTML = iconSvg("trash");
  // Icono generico de tipo de archivo -- no la miniatura real (verla ahi,
  // en "Ajustar imagen" y en las galerias era la misma foto 3 veces).
  $("#media-chip").querySelector(".chip-thumb").innerHTML = iconSvgFilled("fileDescription");
  $("#audio-chip").querySelector(".chip-thumb-audio").innerHTML = iconSvgFilled("fileMusic");
  $("#preview-expand").innerHTML = iconSvg("expand");
  $("#loop-preview-play").innerHTML = iconSvgFilled("playerPlay");
  $("#loop-preview-toggle").innerHTML = iconSvgFilled("playerPause");
  // "Cambiar" (ruta de salida): antes un icono de descargar, que sugeria
  // "bajar algo" -- una carpeta es mas directo para "elegir donde se
  // guarda". "Guardar preset": tenia (por error) el mismo icono de
  // carpeta que ahora usa la ruta de salida; un preset se GUARDA, asi que
  // le toca un icono de guardar (disquete).
  $("#output-browse").innerHTML = iconSvg("folderOpen");
  $$(".preset-dropdown-chevron").forEach((el) => (el.innerHTML = iconSvg("chevronDown")));
  $("#preset-save").innerHTML = iconSvg("deviceFloppy");
  $("#link-btn").innerHTML = iconSvg("cloudDownload");
  $("#preset-save-confirm").innerHTML = iconSvgFilled("check");
  $("#preset-save-cancel").innerHTML = iconSvgFilled("close");
  $("#preset-save-choice-cancel").innerHTML = iconSvgFilled("close");
  $("#output-title-pencil").innerHTML = iconSvg("edit");
  $("#save-cover-btn").innerHTML = iconSvg("download");
  $("#cover-modal-close").innerHTML = iconSvgFilled("close");
  $$(".unit-percent").forEach((el) => (el.innerHTML = iconSvg("percentage", 12)));
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
  lastState = state;
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
  // hay video cargado, asi que no hace falta filtrar aca.
  scheduleLoopPreview();
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
  // cover_available se apaga solo (ver _set_image/_set_video/remove_media
  // en api.py) en cuanto se carga un medio nuevo -- se refleja aca en
  // cada render(), no solo justo despues de exportar (ver onJobDone).
  $("#save-cover-btn").hidden = !state.cover_available;

  const mediaChip = $("#media-chip");
  if (state.media_path) {
    mediaChip.hidden = false;
    mediaChip.querySelector(".chip-name").textContent = titleCase(state.media_filename);
    mediaChip.querySelector(".chip-kind").textContent = state.media_kind_text || "";
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
  if (state.template_path && state.template_box) {
    const [x, y, w, h] = state.template_box;
    el.textContent =
      `Ventana transparente detectada: ${w}x${h} en (${x}, ${y}) · salida 1920x1080`;
    el.hidden = false;
  } else {
    el.hidden = true;
  }
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

// Un solo click en la tarjeta prende/apaga la textura en el video Y la
// deja seleccionada para editar sus controles debajo -- si se apaga, sus
// controles se ocultan (deja de estar "seleccionada").
function toggleTextureLayer(path, layerIndex) {
  markPresetModified();
  if (layerIndex === -1) {
    pywebview.api.add_texture_layer(path).then(() => {
      selectedTexturePath = path;
      refresh();
    });
  } else {
    pywebview.api.remove_texture_layer(layerIndex).then(() => {
      if (selectedTexturePath === path) selectedTexturePath = null;
      refresh();
    });
  }
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
  const index = layers.findIndex((l) => l.path === selectedTexturePath);
  if (index === -1) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const layer = layers[index];
  setTextureBlendLabel(layer.blend);
  $("#texture-opacity-slider").value = layer.opacity;
  $("#texture-opacity-entry").value = layer.opacity;
  $("#texture-scale-slider").value = layer.scale;
  $("#texture-scale-entry").value = layer.scale;
}

function commitTextureOpacity(v) {
  v = Math.max(0, Math.min(100, Math.round(Number(v) || 0)));
  $("#texture-opacity-slider").value = v;
  $("#texture-opacity-entry").value = v;
  const i = currentTextureLayerIndex();
  if (i === -1) return;
  markPresetModified();
  pywebview.api.update_texture_layer(i, { opacity: v });
  Preview.schedulePreview();
  scheduleLoopPreview();
}

function commitTextureScale(v) {
  v = Math.max(10, Math.min(300, Math.round(Number(v) || 100)));
  $("#texture-scale-slider").value = v;
  $("#texture-scale-entry").value = v;
  const i = currentTextureLayerIndex();
  if (i === -1) return;
  markPresetModified();
  pywebview.api.update_texture_layer(i, { scale: v });
  Preview.schedulePreview();
  scheduleLoopPreview();
}

// --------------------------------------------------------------- escala
//
// "Bordes de la imagen" -- el control edita scale_pct (que tan ancho es el
// video dentro del lienzo, ver build_layout en engine.py: mas alto = mas
// ancho = MENOS borde), pero la etiqueta dice "bordes", asi que subir la
// perilla deberia significar MAS borde -- lo contrario de scale_pct. Se
// invierte solo aca, en el limite UI<->Python (240 = 40+200, los extremos
// del rango, asi el valor que ve el usuario se queda en el mismo 40-200):
// Python nunca se entera, sigue recibiendo/guardando scale_pct tal cual.
const SCALE_UI_SUM = 240;

function renderScale(state) {
  const uiValue = SCALE_UI_SUM - state.scale_pct;
  $("#scale-slider").value = uiValue;
  $("#scale-entry").value = uiValue;
  // El control tambien aplica a fotos sueltas (sin plantilla, ver
  // show_scale en api.py) -- "Bordes del video" seria incorrecto ahi.
  $("#scale-section-title").textContent = state.media_is_video ? "Bordes del video" : "Bordes de la imagen";
}

function commitScale(v) {
  v = Math.max(40, Math.min(200, Math.round(Number(v) || 100)));
  $("#scale-slider").value = v;
  $("#scale-entry").value = v;
  markPresetModified();
  pywebview.api.set_scale_pct(SCALE_UI_SUM - v);
  Preview.schedulePreview();
  // Sin esto el fotograma estatico se actualizaba con el borde nuevo pero
  // el loop que ya estaba generado (o el que se generara al tocar play)
  // seguia con el borde viejo -- a diferencia del recorte/velocidad, este
  // control no disparaba una regeneracion del loop.
  scheduleLoopPreview();
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
function positionFloatingDropdown(trigger, list) {
  if (list.parentElement !== document.body) document.body.appendChild(list);
  const margin = 6;
  const rect = trigger.getBoundingClientRect();
  list.style.position = "fixed";
  list.style.left = `${rect.left}px`;
  list.style.width = `${rect.width}px`;
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

function renderOutput(state) {
  if (document.activeElement !== $("#project-title")) {
    $("#project-title").value = state.custom_output_name || "";
  }
  $("#output-label").textContent = state.output_path || "Ruta donde se almacenará el video";
}

// ---------------------------------------------------------- generacion

function setGeneratingUI(active) {
  $("#generate-btn").disabled = active || !(lastState && lastState.ready);
  $("#output-browse").disabled = active;
  $("#cancel-btn").hidden = !active;
  if (active) {
    $("#open-video-btn").hidden = true;
    $("#open-folder-btn").hidden = true;
    $("#save-cover-btn").hidden = true; // la portada del export anterior ya no aplica
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
  $("#status-text").textContent = payload.message;
  $("#status-text").style.color = payload.ok ? "var(--accent)" : (payload.cancelled ? "" : "var(--red)");
  if (payload.ok) {
    $("#open-video-btn").hidden = false;
    $("#open-folder-btn").hidden = false;
    $("#save-cover-btn").hidden = !payload.cover_available;
    $("#progress-fill").style.width = "100%";
  } else {
    $("#progress-bar").hidden = true;
  }
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

function startDownload(url) {
  if (!/^https?:\/\//i.test(url)) {
    $("#download-status").textContent = "Pega un link válido (que empiece con https://).";
    $("#download-status").style.color = "var(--red)";
    return;
  }
  $("#link-btn").disabled = true;
  $("#download-status").textContent = "Descargando video...";
  $("#download-status").style.color = "";
  pywebview.api.download_from_link(url).then((r) => {
    if (!r.ok) {
      $("#link-btn").disabled = false;
      $("#download-status").textContent = r.error;
      $("#download-status").style.color = "var(--red)";
    }
  });
}

window.onDownloadDone = (payload) => {
  $("#link-btn").disabled = false;
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
  $("#loop-preview-play").addEventListener("click", playLoopPreview);
  initLoopPreviewControls();

  ["body", "#preview-dropzone", "#template-section", "#texture-section"]
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
  // El lapiz esta al costado del texto (no superpuesto) -- un click ahi
  // tiene que enfocar igual el input, ya que el input en si ya es
  // editable con solo hacerle click encima.
  $("#output-title-pencil").addEventListener("click", () => $("#project-title").focus());

  // ------------------------------------------------------- archivos
  $("#media-chip").querySelector(".btn-icon").addEventListener("click", () => {
    pywebview.api.remove_media().then((r) => render(r.state));
  });
  $("#audio-chip").querySelector(".btn-icon").addEventListener("click", () => {
    pywebview.api.remove_audio().then((r) => render(r.state));
  });

  // El evento "paste" (no keydown) da acceso directo a clipboardData: si
  // hay texto y es un link, se descarga sin pasar por Python; si no, se
  // delega en Python (imagen/archivos, via PIL.ImageGrab / NSPasteboard).
  document.addEventListener("paste", (e) => {
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
  $("#link-btn").addEventListener("click", () => {
    startDownload($("#link-input").value.trim());
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
  // Antes solo el botoncito (icono) abria el dialogo -- el texto de al
  // lado, que ocupa casi todo el ancho de la fila y PARECE clickeable
  // (mismo estilo que un campo), no hacia nada. Ahora los dos abren el
  // mismo dialogo nativo.
  const browseOutputPath = () => {
    pywebview.api.choose_output_path().then((path) => {
      $("#output-label").textContent = path || "Ruta donde se almacenará el video";
      // El dialogo nativo de "Guardar como" deja escribir el nombre del
      // archivo ahi mismo -- set_chosen_output (api.py) ya sincroniza
      // custom_output_name con eso, pero hace falta refresh() para que
      // "Nombre del video" recoja ese valor nuevo (path por si solo no
      // alcanza, la logica de que nombre corresponde vive en Python).
      refresh();
    });
  };
  $("#output-browse").addEventListener("click", browseOutputPath);
  $("#output-label").addEventListener("click", browseOutputPath);

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
  // Se abre al pasar el cursor (sin click) y se queda abierto mientras el
  // cursor este sobre el boton o sobre el panel -- click sigue funcionando
  // como atajo instantaneo. Ver cancelCoverModalTimers/scheduleCoverModal*.
  $("#save-cover-btn").addEventListener("mouseenter", scheduleCoverModalOpen);
  $("#save-cover-btn").addEventListener("mouseleave", scheduleCoverModalClose);
  $("#save-cover-btn").addEventListener("click", () => {
    cancelCoverModalTimers();
    openCoverModal();
  });
  $("#cover-modal").addEventListener("mouseenter", cancelCoverModalTimers);
  $("#cover-modal").addEventListener("mouseleave", scheduleCoverModalClose);

  // ------------------------------------------------- modal "Guardar portada"
  $("#cover-frame-seek").addEventListener("input", (e) => {
    const video = $("#cover-frame-video");
    if (video.duration) video.currentTime = (e.target.value / 1000) * video.duration;
  });
  $("#cover-mode-control").addEventListener("click", (e) => {
    const btn = e.target.closest(".segmented-item");
    if (!btn) return;
    $$("#cover-mode-control .segmented-item").forEach((b) => b.classList.toggle("segmented-active", b === btn));
  });
  $("#cover-modal-save").addEventListener("click", () => {
    const isVideo = !$("#cover-frame-picker").hidden;
    const loopTime = isVideo ? $("#cover-frame-video").currentTime : null;
    const modeBtn = $("#cover-mode-control .segmented-active");
    const mode = modeBtn ? modeBtn.dataset.value : "full";
    closeCoverModal();
    saveCoverNow(loopTime, mode);
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

// ---------------------------------------------------------- previsualizador
//
// Estado vacio (dropzone) vs. cargado (fotograma real que empuja Python
// via window.onPreviewReady). schedulePreview() debounca las llamadas a
// Api.request_preview() -- mismo criterio que el "self.after" de Tk en
// la version vieja (_schedule_preview), pero con setTimeout.

const Preview = (() => {
  let timer = null;

  function schedulePreview(delay = 300) {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      if (typeof pywebview !== "undefined") pywebview.api.request_preview();
    }, delay);
  }

  function applyState(state) {
    const hasMedia = !!state.media_path;
    $("#preview-dropzone").hidden = hasMedia;
    $("#preview-expand").hidden = !hasMedia;
    if (!hasMedia) {
      $("#preview-image").hidden = true;
      hideLoopPreview();
      if (window.tintBackgroundFromImage) window.tintBackgroundFromImage(null);
      if (window.setHalftoneBackgroundImage) window.setHalftoneBackgroundImage(null);
      if (window.setAdaptiveAccent) window.setAdaptiveAccent(null);
    }
  }

  function onReady(payload) {
    const dataUri = payload && payload.data_uri;
    if (!dataUri) return;
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
  function toggleExpand() {
    const surface = $("#preview-surface");
    if (document.fullscreenElement === surface) {
      document.exitFullscreen();
    } else {
      surface.requestFullscreen().catch(() => {});
    }
  }

  function init() {
    $("#preview-dropzone").addEventListener("click", () => {
      pywebview.api.browse_media().then(handleResult);
    });
    $("#preview-expand").addEventListener("click", toggleExpand);
    // Actualiza el icono/titulo del boton tanto al entrar como al salir --
    // cubre el click del boton, la tecla Esc, Y salir por otras vias del
    // sistema (ej. otro atajo de fullscreen), que no pasan por
    // toggleExpand() pero SI disparan este evento.
    document.addEventListener("fullscreenchange", () => {
      const isFull = document.fullscreenElement === $("#preview-surface");
      const btn = $("#preview-expand");
      btn.innerHTML = iconSvg(isFull ? "shrink" : "expand");
      btn.title = isFull ? "Salir de pantalla completa" : "Agrandar";
    });
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

// Ruta del ultimo fragmento generado y listo para verse -- null si todavia
// no hay ninguno, o si el que habia quedo invalido por un cambio de
// estado. Controla si #loop-preview-play se muestra.
let loopPreviewPath = null;

function hideLoopPreview() {
  loopPreviewPath = null;
  $("#loop-preview-play").hidden = true;
  $("#loop-preview-controls").hidden = true;
  const video = $("#loop-preview-video");
  if (video.hidden) return;
  video.pause();
  video.hidden = true;
  video.removeAttribute("src");
  video.load();
  $("#preview-image").hidden = false;
}

// Sin autoplay: se regenera solo (con el mismo debounce que el fotograma
// estatico) cada vez que cambia el recorte o la velocidad (ver
// LoopSlider.notify() y el listener de #speed-control), pero el usuario
// decide cuando verlo -- #loop-preview-play es la unica forma de
// reproducirlo, nunca se dispara solo.
let loopPreviewTimer = null;
function scheduleLoopPreview(delay = 400) {
  if (loopPreviewTimer) clearTimeout(loopPreviewTimer);
  loopPreviewTimer = setTimeout(() => {
    loopPreviewTimer = null;
    if (typeof pywebview === "undefined") return;
    // request_loop_preview() solo devuelve {ok:false} (o rechaza) cuando
    // NO va a mandar onLoopPreviewReady (por ejemplo, todavia analizando
    // el clip recien cargado) -- sin boton que resetear, un fallo aca se
    // ignora en silencio (es un refresco de fondo, no una accion del
    // usuario); el proximo cambio de recorte/velocidad vuelve a intentar.
    pywebview.api.request_loop_preview().catch(() => {});
  }, delay);
}

window.onLoopPreviewReady = function (payload) {
  const path = payload && payload.path;
  if (coverModalWaitingForPreview) {
    coverModalWaitingForPreview = false;
    if (path) {
      const coverVideo = $("#cover-frame-video");
      coverVideo.src = buildFileUrl(path);
      coverVideo.addEventListener(
        "loadedmetadata",
        () => {
          $("#cover-frame-seek").disabled = false;
          $("#cover-frame-loading").hidden = true;
        },
        { once: true }
      );
    } else {
      $("#cover-frame-loading").textContent = "No se pudo generar la vista previa.";
    }
  }
  if (!path) {
    if (payload && payload.error) console.error("[loop preview] ffmpeg:", payload.error);
    return;
  }
  loopPreviewPath = path;
  const video = $("#loop-preview-video");
  if (!video.hidden) {
    // Ya se estaba viendo un fragmento anterior -- se actualiza en vivo
    // en vez de tirarlo de vuelta al fotograma estatico.
    video.src = buildFileUrl(path);
    video.play();
    return;
  }
  $("#loop-preview-play").hidden = false;
};

function playLoopPreview() {
  if (!loopPreviewPath) return;
  const video = $("#loop-preview-video");
  video.src = buildFileUrl(loopPreviewPath);
  $("#loop-preview-play").hidden = true;
  $("#preview-image").hidden = true;
  video.hidden = false;
  $("#loop-preview-controls").hidden = false;
  video.play();
}

// Controles propios de pausa/reproducir + buscar momento (ver comentario en
// index.html sobre por que no se usan los <video controls> nativos). Se
// atan a los eventos reales del <video> (play/pause/timeupdate) en vez de
// llevar su propio estado, asi nunca se desincronizan del video real.
function initLoopPreviewControls() {
  const video = $("#loop-preview-video");
  const toggle = $("#loop-preview-toggle");
  const seek = $("#loop-preview-seek");
  let scrubbing = false;

  toggle.addEventListener("click", () => {
    if (video.paused) video.play(); else video.pause();
  });
  video.addEventListener("play", () => (toggle.innerHTML = iconSvgFilled("playerPause")));
  video.addEventListener("pause", () => (toggle.innerHTML = iconSvgFilled("playerPlay")));
  video.addEventListener("timeupdate", () => {
    if (scrubbing || !video.duration) return;
    seek.value = Math.round((video.currentTime / video.duration) * 1000);
  });
  seek.addEventListener("input", () => {
    scrubbing = true;
    if (video.duration) video.currentTime = (seek.value / 1000) * video.duration;
  });
  seek.addEventListener("change", () => {
    scrubbing = false;
  });
}

// --------------------------------------------------- modal "Guardar portada"
// Se abre desde #save-cover-btn (solo visible justo despues de exportar con
// exito, ver cover_available en api.py). Si no hay nada que elegir (foto
// suelta sin plantilla: ni momento del loop ni "parte vacia" tienen
// sentido) se salta el modal y guarda directo, igual que antes.

let coverModalWaitingForPreview = false;

// Ancla el panel al boton que lo abre en vez de centrarlo -- pegado al
// borde DERECHO del boton, desplegado hacia ARRIBA (el boton vive abajo
// del todo, pegado al de "Generar video"). Misma logica que uso la rama de
// erick para su panel "Generar video" antes de revertirla.
function positionNearTrigger(trigger, panel) {
  const margin = 10;
  const rect = trigger.getBoundingClientRect();
  const panelRect = panel.getBoundingClientRect();
  let left = rect.right - panelRect.width;
  left = Math.max(margin, Math.min(left, window.innerWidth - panelRect.width - margin));
  let top = rect.top - margin - panelRect.height;
  if (top < margin) top = rect.bottom + margin; // sin lugar arriba -- cae abajo
  panel.style.left = `${Math.round(left)}px`;
  panel.style.top = `${Math.round(top)}px`;
}

function saveCoverNow(loopTime, mode) {
  pywebview.api.save_cover(loopTime, mode).then((r) => {
    if (!r.ok) $("#status-text").textContent = r.error || "No se pudo guardar la portada.";
  });
}

function closeCoverModal() {
  const modal = $("#cover-modal");
  if (modal.hidden) return;
  animateDropdown(modal, false); // pone [hidden] solo al terminar la animacion
  coverModalWaitingForPreview = false;
  const video = $("#cover-frame-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
}

function setupCoverFramePicker() {
  const video = $("#cover-frame-video");
  const seek = $("#cover-frame-seek");
  video.pause();
  video.removeAttribute("src");
  video.load();
  seek.disabled = true;
  seek.value = 0;
  $("#cover-frame-loading").hidden = false;
  $("#cover-frame-loading").textContent = "Cargando vista previa del loop...";
  coverModalWaitingForPreview = true;
  // Se pide un fragmento nuevo (no se reusa el que ya estuviera cargado en
  // el previsualizador principal) para que siempre refleje el recorte y la
  // velocidad ACTUALES -- ver onLoopPreviewReady mas abajo, que lo entrega
  // por el mismo evento de siempre.
  pywebview.api.request_loop_preview().then((r) => {
    if (!r || !r.ok) {
      coverModalWaitingForPreview = false;
      $("#cover-frame-loading").textContent = "No se pudo generar la vista previa.";
    }
  });
}

function openCoverModal() {
  const isVideo = !!(lastState && lastState.media_is_video);
  const hasTemplate = !!(lastState && lastState.template_path);
  if (!isVideo && !hasTemplate) {
    // Nada que elegir (foto sin plantilla) -- se guarda directo, sin panel.
    saveCoverNow(null, "full");
    return;
  }
  const modal = $("#cover-modal");
  if (!modal.hidden) return; // ya esta abierto (el mouse volvio a entrar)
  $$("#cover-mode-control .segmented-item").forEach((b) => {
    b.classList.toggle("segmented-active", b.dataset.value === "full");
  });
  $("#cover-frame-picker").hidden = !isVideo;
  $("#cover-mode-picker").hidden = !hasTemplate;
  modal.hidden = false;
  positionNearTrigger($("#save-cover-btn"), modal);
  animateDropdown(modal, true);
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

function scheduleCoverModalClose() {
  if (coverModalHoverTimer) {
    clearTimeout(coverModalHoverTimer);
    coverModalHoverTimer = null;
  }
  if (coverModalCloseTimer) clearTimeout(coverModalCloseTimer);
  coverModalCloseTimer = setTimeout(() => {
    coverModalCloseTimer = null;
    closeCoverModal();
  }, 220);
}

window.onPreviewReady = Preview.onReady;

// ------------------------------------------------------- ajustar imagen
//
// Reimplementacion en canvas del ImageFocusPicker de Tk (app.py): mismo
// algoritmo pixel a pixel (ver _focus_rect/_final_rect alla), asi que el
// recuadro que se ve aca coincide exactamente con lo que exporta ffmpeg.
// La geometria vive en JS; Python solo guarda el resultado (set_focus).

const FocusPicker = (() => {
  const W = 352, H = 200; // debe coincidir con FOCUS_PICKER_W/H en api.py
  const HANDLE = 5;       // medio lado del tirador de esquina (dibujo)
  const HIT = 11;         // radio de agarre de una esquina (interaccion)
  let canvas, ctx;
  let image = null;
  let imgW = W, imgH = H; // tamano ajustado (fit) de la imagen dentro del canvas
  let zoom = 100, focusX = 0.5, focusY = 0.5;
  let lastPreviewUri = null;
  // null | {mode:"move"} | {mode:"resize", ax, ay} (ancla = esquina opuesta)
  let drag = null;

  function clamp01(v) {
    return Math.max(0, Math.min(1, v));
  }

  function minDim() {
    return Math.min(imgW, imgH);
  }

  function origin() {
    return [(W - imgW) / 2, (H - imgH) / 2];
  }

  // Recuadro CUADRADO del recorte -- exactamente lo que exporta ffmpeg
  // (build_focus_crop): lado min(iw,ih)/zoom colocado con focusX/focusY.
  function cropRect() {
    const side = minDim() * (100 / zoom);
    const [ox, oy] = origin();
    const x = ox + (imgW - side) * focusX;
    const y = oy + (imgH - side) * focusY;
    return [x, y, side];
  }

  // "Ajustar imagen" (posicion/zoom del recorte) y "Bordes de la imagen"
  // son controles independientes: el borde solo afecta como se compone el
  // recorte YA HECHO sobre el lienzo final (letterbox en los lados), no
  // que parte de la foto se puede seleccionar aca -- por eso este widget
  // ya no lo toma en cuenta para nada, ni al dibujar ni al arrastrar.
  function finalRect() {
    const [x, y, side] = cropRect();
    return [x, y, x + side, y + side];
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
    // tiradores de esquina (cuadraditos) para redimensionar
    for (const c of corners()) {
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(c.x - HANDLE, c.y - HANDLE, HANDLE * 2, HANDLE * 2);
      ctx.strokeStyle = "rgba(0,0,0,0.35)";
      ctx.lineWidth = 1;
      ctx.strokeRect(c.x - HANDLE + 0.5, c.y - HANDLE + 0.5, HANDLE * 2 - 1, HANDLE * 2 - 1);
    }
  }

  function notify() {
    pywebview.api.set_focus(Math.round(zoom), focusX, focusY);
    Preview.schedulePreview();
  }

  function canvasPos(e) {
    const rect = canvas.getBoundingClientRect();
    return [e.clientX - rect.left, e.clientY - rect.top];
  }

  function hitTest(px, py) {
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

  function applyResize(px, py, ax, ay) {
    // lado deseado del cuadrado segun el eje dominante del gesto
    let side = Math.max(Math.abs(px - ax), Math.abs(py - ay));
    side = Math.max(minDim() / 3, Math.min(minDim(), side)); // zoom 100..300
    zoom = (100 * minDim()) / side;
    // la esquina ancla queda clavada en su lugar
    const x = px >= ax ? ax : ax - side;
    const y = py >= ay ? ay : ay - side;
    const [ox, oy] = origin();
    focusX = imgW - side < 1 ? 0.5 : clamp01((x - ox) / (imgW - side));
    focusY = imgH - side < 1 ? 0.5 : clamp01((y - oy) / (imgH - side));
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

  function applyState(state) {
    zoom = state.focus_zoom_pct || 100;
    focusX = state.focus_x ?? 0.5;
    focusY = state.focus_y ?? 0.5;
    loadImage(state.show_focus ? state.media_focus_preview : null);
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
      const side = minDim() * (100 / zoom);
      const rangeX = Math.max(1, imgW - side);
      const rangeY = Math.max(1, imgH - side);
      focusX = clamp01(focusX + e.movementX / rangeX);
      focusY = clamp01(focusY + e.movementY / rangeY);
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
      pywebview.api.reset_focus().then((r) => applyState(r.state));
    });
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
